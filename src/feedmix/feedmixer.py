"""
Instances of `FeedMixer` are initialized with a list of Atom/RSS feeds and
generate an Atom/RSS/JSON feed consisting of the most recent `num_keep` entries
from each.

Usage
-----

First initialize the `FeedMixer` object with its metadata and list of feeds::

>>> from feedmixer import FeedMixer
>>> title = "Title"
>>> link = "http://example.com/feedmixer/feed"
>>> desc = "Description of feed"
>>> feeds = ['http://americancynic.net/atom.xml', 'http://hnrss.org/newest']
>>> fm = FeedMixer(title=title, link=link, desc=desc, feeds=feeds)

Nothing is fetched until you ask for the list of mixed entries or for a feed to
be generated:

>>> mixed = fm.mixed_entries
>>> # The first time there will be a pause here while the
>>> # feeds are fetched over the network. On subsequent calls,
>>> # feeds will likely be returned from the cache quickly.
>>> len(mixed)
6

Feeds of various flavours are generated by calling one of the following methods:

    - `atom_feed()`
    - `rss_feed()`
    - `json_feed()`

>>> atom_feed = fm.atom_feed()
>>> atom_feed
'<?xml version="1.0" encoding="utf-8"?>...and so on...'

Feeds are fetched in parallel (using threads).

If any of the `feeds` URLs cannot be fetched or parsed, the errors will be
reported in the `error_urls` attribute.

To set a timeout on network requests, do this in your app::

>>> TIMEOUT = 120  # time to wait for http requests (seconds)
>>> import socket
>>> socket.setdefaulttimeout(TIMEOUT)

Interface
---------
"""
import datetime
import logging
import functools
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import json
from typing import Type, List, Optional, Callable, Dict, Union

# https://docs.djangoproject.com/en/1.10/_modules/django/utils/feedgenerator/
import feedgenerator
from feedgenerator import Rss201rev2Feed, Atom1Feed, SyndicationFeed
from jsonfeed import JSONFeed

import feedparser
from feedparser.util import FeedParserDict

import requests
import requests.utils
from requests.exceptions import RequestException

# Memoize results from parser
# TODO: make maxsize user-configurable
@functools.lru_cache(maxsize=128)
def cache_parser(text):
    return feedparser.parse(text)

# Types:
class ParseError(Exception): pass
FCException = Union[Exception, ParseError]
error_dict_t = Dict[str, FCException]

logger = logging.getLogger(__name__)

class FeedMixer(object):
    def __init__(self, title='Title', link='', desc='',
                 feeds: List[Optional[str]]=[], num_keep=3, prefer_summary=True,
                 max_threads=10, max_feeds=100, sess: requests.Session=None) -> None: 
        """
        __init__(self, title, link='', desc='', feeds=[], num_keep=3, \
            max_thread=5, max_feeds=100,
            sess=requests.Session())

        Args:
            title: the title of the generated feed
            link: the URL of the generated feed
            desc: the description of the generated feed
            feeds: the list of feed URLs to fetch and mix
            num_keep: the number of entries to keep from each member of `feeds`
            prefer_summary: If True, prefer the (short) 'summary'; otherwise
                prefer the (long) feed 'content'.
            max_threads: the maximum number of threads to spin up while fetching
            feeds
            max_feeds: the maximum number of feeds to fetch
                injectable for testing purposes)
            sess: the requests.session object to use for making http GET
                requests. You can pass in a session object that caches results (see
                the cachecontrol package) or sets custom headers, etc. If not
                set, a new default session will be used per request.
        """
        self.title = title
        self.link = link
        self.desc = desc
        self.max_feeds = max_feeds
        self._feeds = feeds[:max_feeds]
        self._num_keep = num_keep
        self.prefer_summary = prefer_summary
        self.max_threads = max_threads
        self._mixed_entries = []  # type: List[Optional[dict]]
        self._error_urls = {}  # type: error_dict_t
        if sess is None:
            sess = requests.Session()
        self.sess = sess

        self.sess.headers.update({
            'User-Agent': 'feedmixer (github.com/cristoper/feedmixer)'
        })

    @property
    def num_keep(self) -> int:
        """
        The number of entries to keep from each feed in `feeds`. Setting this
        property will trigger the feeds to be re-fetched.
        """
        return self._num_keep

    @num_keep.setter
    def num_keep(self, value: int) -> None:
        self._num_keep = value
        self.feeds = self._feeds

    @property
    def mixed_entries(self) -> List[Optional[dict]]:
        """
        The parsed feed entries fetched from the list of URLs in `feeds`.
        (Accessing the property triggers the feeds to be fetched if they
        have not yet been.)
        """
        if len(self._mixed_entries) < 1:
            self.__fetch_entries()
        return self._mixed_entries

    @property
    def error_urls(self) -> error_dict_t:
        """
        A dictionary whose keys are the URLs which generated an error (if any
        did), and whose associated values are an Exception object which contains
        a description of the error (and http status code if applicable).
        """
        return self._error_urls

    @property
    def feeds(self) -> list:
        """
        Get or set list of feeds.
        """
        return self._feeds

    @feeds.setter
    def feeds(self, value: List[Optional[str]]) -> None:
        """
        Reset _mixed_entries whenever we get a new list of feeds.
        """
        self._feeds = value[:self.max_feeds]
        self._mixed_entries = []

    def atom_feed(self) -> str:
        """
        Returns:
            An Atom feed consisting of the `num_keep` most recent entries from
            each of the `feeds`.
        """
        return self.__generate_feed(Atom1Feed).writeString('utf-8')

    def rss_feed(self) -> str:
        """
        Returns:
            An RSS 2 feed consisting of the `num_keep` most recent entries from
            each of the `feeds`.
        """
        return self.__generate_feed(Rss201rev2Feed).writeString('utf-8')

    def json_feed(self) -> str:
        """
        Returns:
            A JSON dict consisting of the `num_keep` most recent entries from
            each of the `feeds`.
        """
        return self.__generate_feed(JSONFeed).writeString('utf-8')

    def __fetch_entries(self) -> None:
        """
        Multi-threaded fetching of the `feeds`. Keeps the `num_keep` most recent
        entries from each feed, combines them (sorted chronologically), extracts
        `feedgernerator`-compatible metadata, and then stores the list of
        entries as `self.mixed_entries`
        """
        parsed_entries = []  # type: List[dict]
        self._error_urls = {}

        def fetch(url):
            r = self.sess.get(url)
            r.raise_for_status()
            # NOTE: I tried doing the parsing here in the threads, but it was
            # actually a bit slower than doing it all serially on the main
            # thread.
            return r

        with ThreadPoolExecutor(max_workers=self.max_threads) as exec:
            future_to_url = {exec.submit(fetch, url):
                    url for url in self.feeds}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                logger.info("Fetched {}".format(url))
                try:
                    resp = future.result()
                    f = cache_parser(resp.text)

                    logger.debug(cache_parser.cache_info())
                    logger.info("Got feed from feedparser {}".format(url))
                    #logger.debug("Feed: {}".format(f))

                    parse_err = len(f.get('entries')) == 0 and f.get('bozo')
                    if f is None or parse_err:
                        logger.info("Parse error ({})"
                                    .format(f.get('bozo_exception')))
                        raise ParseError("Parse error: {}"
                                         .format(f.get('bozo_exception')))


                    if self._num_keep < 1:
                        newest = f.entries
                    else:
                        newest = f.entries[0:self._num_keep]

                    for e in newest:
                        e['feed_link'] = f.feed.link
                        e['feed_title'] = f.feed.title
                        if 'author_detail' not in e:
                            # use feed author if individual entries are missing
                            # author property
                            if 'author_detail' in f.feed:
                                e['author_detail'] = f.feed.author_detail
                                e.author_detail = f.feed.author_detail

                    parsed_entries += newest
                except Exception as e:
                    # will be ParseError, RequestException, or an exception
                    # from threadpool
                    self._error_urls[url] = e
                    logger.info("{} generated an exception: {}".format(url, e))

        # sort entries by published date (with fall back to updated date)
        parsed_entries.sort(key=lambda e: e.get('published') or e.get('updated') or "",
                            reverse=True)

        # extract metadata into a form usable by feedgenerator
        mixed_entries = self.extract_meta(parsed_entries, self.prefer_summary)
        self._mixed_entries = mixed_entries

    @staticmethod
    def extract_meta(parsed_entries: List[dict],
                     prefer_summary=True) -> List[Optional[dict]]:
        """
        Convert a FeedParserDict object into a dict compatible with the Django
        feedgenerator classes.

        Args:
            parsed_entries: List of entries from which to extract meta data.
            prefer_summary: If True, prefer the (short) 'summary'; otherwise
                prefer the (long) 'content'.
        """
        mixed_entries = [] # type: List[Optional[dict]]
        for e in parsed_entries:
            metadata = {}

            # title, link, and description are mandatory
            metadata['title'] = e.get('title', '')
            metadata['link'] = e.get('link', '')

            summary = e.get('summary')
            content = e.get('content')
            if content:
                # atom feeds can have several content tags, each with a
                # different type. We just use the first one.
                content = content[0].get('value')
            if prefer_summary:
                content = summary or content
            else:
                content = content or summary
            metadata['description'] = content

            if 'author_detail' in e:
                metadata['author_email'] = e['author_detail'].get('email')
                metadata['author_name'] = e['author_detail'].get('name')
                metadata['author_link'] = e['author_detail'].get('href')

            # Keep original feed info (this is only serialized in the JSON feed)
            metadata['feed_link'] = e['feed_link']
            metadata['feed_title'] = e['feed_title']

            # convert time_struct tuples into datetime objects
            # (the min() prevents error in the off-chance that the
            # date contains a leap-second)
            tp = e.get('published_parsed')
            if tp:
                metadata['pubdate'] = datetime.datetime(*tp[:5] + (min(tp[5],
                                                                       59),))

            tu = e.get('updated_parsed')
            if tu:
                metadata['updateddate'] = datetime.datetime(*tu[:5] +
                                                            (min(tu[5], 59),))

            metadata['comments'] = e.get('comments')
            metadata['unique_id'] = e.get('id')
            metadata['item_copyright'] = e.get('license')

            if 'tags' in e:
                taglist = [tag.get('term') for tag in e['tags']]
                metadata['categories'] = taglist
            if 'enclosures' in e:
                enclist = []
                for enc in e['enclosures']:
                    enclist.append(feedgenerator.Enclosure(enc.href, enc.length,
                                                           enc.type))
                metadata['enclosures'] = enclist
                if len(enclist) > 0:
                    # The current standalone version of feedgenerator does not
                    # handle 'enclosures' only a single 'enclosure'
                    metadata['enclosure'] = enclist[0]

            mixed_entries.append(metadata)
        return mixed_entries

    def __generate_feed(self, gen_cls: Type[SyndicationFeed])-> SyndicationFeed:
        """
        Generate a feed using one of the generator classes from the Django
        `feedgenerator` module.
        """
        gen = gen_cls(title=self.title, link=self.link, description=self.desc)
        for e in self.mixed_entries:
            gen.add_item(**e)
        return gen
