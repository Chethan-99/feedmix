import setuptools



setuptools.setup(
    name='feedmix',
    version='0.0.1',
    author='Christoper',
    author_email='test@test.com',
    description='Testing installation of Package',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url='https://github.com/Chethan-99/feedmix',
    project_urls = {
        "Bug Tracker": "https://github.com/Chethan-99/feedmix/issues"
    },
    license='MIT',
    packages=['feedmix'],
    install_requires=['CacheControl==0.12.10','certifi==2021.10.8','chardet==4.0.0','charset-normalizer==2.0.9','django-jsonfeed==0.3.1','falcon==3.0.1','feedgenerator==2.0.0','feedparser @ git+git://github.com/cristoper/feedparser.git@6e35ff74d2d18eaf622df495f051d7e62db66b3b','gevent==21.12.0','greenlet==1.1.2','gunicorn==20.1.0','idna==3.3','msgpack==1.0.3','pytz==2021.3','requests==2.26.0','sgmllib3k==1.0.0','six==1.16.0','typing==3.7.4.3','urllib3==1.26.7','zope.event==4.5.0','zope.interface==5.4.0'],
)