from __future__ import unicode_literals
from .settings import settings

from django.db import models
from django.core.urlresolvers import reverse
from django.utils.http import urlquote

import os
import urllib
import time
import re
from datetime import timedelta
from bson.binary import Binary

import utils
from .utils import getdb, format_size, format_date, scrape, random_token, normalize_name
import const


class UserPref(models.Model):

    user = models.OneToOneField(settings.AUTH_USER_MODEL, unique=True, on_delete=models.CASCADE)
    token = models.CharField(max_length=32, unique=True, default=random_token)


class TorrentsList(object):

    torrents = None
    order_by = None
    order_url = None
    asc = True

    def __init__(
        self, cursor, url, page=1, max_results=None, order_by=None,
        asc=True, order_url=None, request=None, page_size=settings.BTDHT_PAGE_SIZE
    ):
        self._request = request
        if request is not None:
            self._timezone = request.COOKIES.get('timezone', 'UTC')
        else:
            self._timezone = 'UTC'
        self.order_url = order_url
        if order_by:
            self.order_by = order_by
            self.asc = asc
            if order_by == const.ORDER_BY_SCORE:
                cursor = cursor.sort([("score", {"$meta": "textScore"})])
            elif order_by == const.ORDER_BY_NAME:
                cursor = cursor.sort([("name", 1 if asc else -1)])
            elif order_by == const.ORDER_BY_SIZE:
                cursor = cursor.sort([("size", 1 if asc else -1)])
            elif order_by == const.ORDER_BY_CREATED:
                cursor = cursor.sort([("created", 1 if asc else -1)])
            elif order_by == const.ORDER_BY_FILES:
                cursor = cursor.sort([("file_nb", 1 if asc else -1)])
            elif order_by == const.ORDER_BY_PEERS:
                cursor = cursor.sort([("peers", 1 if asc else -1)])
            elif order_by == const.ORDER_BY_SEEDS:
                cursor = cursor.sort([("seeds", 1 if asc else -1)])
            else:
                self.order_by = None
        skip = page_size * (page - 1)
        limit = page_size
        self.page = page
        self._cursor = cursor.skip(skip).limit(limit)
        if max_results is not None:
            self.size = min(cursor.count(), max_results)
        else:
            self.size = cursor.count()
        self.start = skip
        self.end = skip + limit if limit > 0 else self.size

        self.last_page = int(self.size/page_size) + 1

        self.start_page = max(1, page - 26)
        self.end_page = min(self.last_page, page + 26)

        self.url = url

    def _url_sort_by(self, field, prefere_asc=True):
        if self.order_url is None:
            return None
        if self.order_by == field:
            asc = '0' if self.asc else '1'
        else:
            asc = '1' if prefere_asc else '0'
        return self.order_url(field, asc)

    def url_sort_by_score(self):
        return self._url_sort_by(const.ORDER_BY_SCORE)

    def url_sort_by_name(self):
        return self._url_sort_by(const.ORDER_BY_NAME)

    def url_sort_by_size(self):
        return self._url_sort_by(const.ORDER_BY_SIZE)

    def url_sort_by_created(self):
        return self._url_sort_by(const.ORDER_BY_CREATED)

    def url_sort_by_files(self):
        return self._url_sort_by(const.ORDER_BY_FILES)

    def url_sort_by_peers(self):
        return self._url_sort_by(const.ORDER_BY_PEERS, False)

    def url_sort_by_seeds(self):
        return self._url_sort_by(const.ORDER_BY_SEEDS, False)

    def __iter__(self):
        if self.torrents is None:
            self.torrents = []
            torrents = []
            to_scrape = []
            for result in self._cursor:
                torrents.append(result)
                if (
                    'last_scrape' not in result or
                    result['last_scrape'] == 0 or
                    (time.time() - result['last_scrape']) > settings.BTDHT_SCRAPE_BROWSE_INTERVAL
                ):
                    to_scrape.append(str(result['_id']))
            if settings.BTDHT_LIVE_SCRAPE and to_scrape:
                scrape_result = scrape(to_scrape)
                for result in torrents:
                    result.update(scrape_result.get(str(result['_id']), {}))
                    torrent = Torrent(
                        obj=result,
                        no_files=True,
                        request=self._request
                    )
                    self.torrents.append(torrent)
                    yield torrent
            else:
                for result in torrents:
                    torrent = Torrent(
                        obj=result,
                        no_files=True,
                        request=self._request
                    )
                    self.torrents.append(torrent)
                    yield torrent
        else:
            for torrent in self.torrents:
                yield torrent

    def pages(self):
        if self.has_previous_page():
            yield {'url': self.url(self.page - 1), 'class': "pagination-prev", 'name': "&laquo;"}
        else:
            yield {'url': None, 'class': "pagination-prev disabled", 'name': "&laquo;"}
        if self.show_start_suspension():
            yield {
                'url': self.url(1),
                'class': "active" if self.page == 1 else None,
                'name': 1
            }
            yield {'url': None, 'class': "disabled", 'name': "..."}
        for i in xrange(self.start_page, self.end_page + 1):
            yield {
                'url': self.url(i),
                'class': "active" if self.page == i else None,
                'name': i
            }
        if self.show_end_suspension():
            yield {'url': None, 'class': "disabled", 'name': "..."}
            yield {
                'url': self.url(self.last_page),
                'class': "active" if self.page == self.last_page else None,
                'name': self.last_page
            }
        if self.has_next_page():
            yield {'url': self.url(self.page + 1), 'class': "pagination-next", 'name': "&raquo;"}
        else:
            yield {'url': None, 'class': "pagination-next disabled", 'name': "&raquo;"}

    def has_previous_page(self):
        return self.page > 1

    def has_next_page(self):
        return self.page < self.last_page

    def show_start_suspension(self):
        return self.start_page > 2

    def show_end_suspension(self):
        return self.end_page < (self.last_page - 1)

    def data(self):
        return [torrent.data() for torrent in self]


# Create your models here.
class Torrent(object):

    score = None

    hash = None
    name = None
    files = None
    created = None
    file_nb = None
    size = None
    categories = None
    dmca_deleted = None

    seeds = None
    peers = None
    complete = None

    last_scrape = 0

    def data(self):
        is_auth = self._request.user.is_authenticated()
        if settings.BTDHT_REQUIRE_AUTH and not is_auth:
            return {}
        data = {
            'hash': self.hash.encode('hex'),
            'name': self.name,
            'created': self.created,
            'file_nb': self.file_nb,
            'size': self.size,
            'seeds': self.seeds,
            'peers': self.peers,
            'complete': self.complete,
            'categories': self.categories,
            'last_scrape': self.last_scrape
        }
        if self.files is not None:
            data['files'] = self.files
        if self.score is not None:
            data['score'] = self.score
        if not settings.BTDHT_HIDE_MAGNET_FROM_UNAUTH or is_auth:
            data['magnet'] = self.magnet
        if not settings.BTDHT_HIDE_TORRENT_LINK_FROM_UNAUTH or is_auth:
            data['url'] = self.url
        return data

    @staticmethod
    def search(query, page=1, order_by=const.ORDER_BY_SCORE, asc=True, category=0, request=None):
        db = getdb()
        search_query = {}

        if re.match("^[0-9A-Fa-f]{40}$", query):
            search_query = {"$or": [
                {"$text": {"$search": query, '$language': "english"}},
                {"_id": Binary(query.decode("hex"))}
            ]}
        else:
            search_query = {"$text": {"$search": query, '$language': "english"}}
        if category > 0:
            search_query = {"$and": [search_query, {'categories': const.categories[category-1]}]}
        results = db.find(search_query, {"score": {"$meta": "textScore"}, 'files': False})
        if re.match("^[0-9A-Fa-f]{40}$", query) and results.count() == 0:
            results = getdb("torrents_ban").find(
                {'_id': Binary(query.decode("hex"))},
                {"score": {"$meta": "textScore"}, 'files': False}
            )
        return TorrentsList(
            results,
            url=lambda page: reverse(
                "btdht_search:index_query",
                kwargs=dict(
                    page=page, query=query, order_by=order_by,
                    asc=1 if asc else 0, category=category
                )
            ) + '#results',
            order_url=lambda order_by, asc: reverse(
                "btdht_search:index_query",
                kwargs=dict(page=page, query=query, order_by=order_by, asc=asc, category=category)
            ) + '#results',
            page=page,
            order_by=order_by,
            asc=asc,
            request=request
        )

    @staticmethod
    def _list(query, sort, url, page, max_results, request, page_size=settings.BTDHT_PAGE_SIZE):
        db = getdb()
        results = db.find(query, {'files': False}).sort(
            sort
        )
        return TorrentsList(
            results,
            url=url,
            page=page,
            max_results=max_results,
            request=request,
            page_size=page_size
        )

    @classmethod
    def recent(cls, page, category=0, max_results=None, request=None, page_size=settings.BTDHT_PAGE_SIZE):
        if category > 0:
            search_query = {'categories': const.categories[category-1]}
        else:
            search_query = {}
        return cls._list(	
            search_query, [("created", -1)],
            lambda page: reverse("btdht_search:recent", args=[category, page]) + '#recent',
            page, max_results, request, page_size
        )

    @classmethod
    def top(cls, page, category=0, max_results=None, request=None, page_size=settings.BTDHT_PAGE_SIZE):
        search_query = {"seeds_peers": {"$gt": 0}}
        if category > 0:
            search_query['categories'] = const.categories[category-1]
        return cls._list(
            search_query, [("seeds_peers", -1), ("seeds", -1)],
            lambda page: reverse("btdht_search:top", args=[category, page]),
            page, max_results, request, page_size
        )

    def __init__(self, hash=None, obj=None, no_files=False, request=None):
        self._request = request
        if request is not None:
            self._timezone = request.COOKIES.get('timezone', 'UTC')
        else:
            self._timezone = 'UTC'
        if obj is None and hash is not None:
            results = getdb().find({"_id": Binary(hash)})
            if results.count() == 0:
                results = getdb("torrents_ban").find({'_id': utils.Binary(hash)})
            if results.count() != 1:
                raise ValueError("Torrent for hash %r not found" % hash)
            obj = results[0]
        if obj is not None:
            self.score = obj.get("score")
            self.hash = obj['_id']
            self.name = obj['name']
            self.size = obj['size']
            self.created = obj['created']
            self.file_nb = obj['file_nb']
            if no_files is False:
                self.files = obj['files']

            self.seeds = obj.get('seeds')
            self.peers = obj.get('peers')
            self.complete = obj.get('complete')
            self.last_scrape = obj.get('last_scrape', 0)
            self.categories = obj.get('categories')
            self.dmca_deleted = obj.get('dmca_deleted')
        else:
            raise ValueError("missing value to initialize Torrent object")

    def scrape(self):
        if time.time() - self.last_scrape > settings.BTDHT_SCRAPE_MIN_INTERVAL:
            result = scrape([self.hash])
            self.seeds = result[self.hash]['seeds']
            self.peers = result[self.hash]['peers']
            self.complete = result[self.hash]['complete']
            self.last_scrape = result[self.hash]['last_scrape']

    @property
    def hex_hash(self):
        return self.hash.encode("hex").lower()

    @property
    def magnet(self):
        trackers = "&".join("tr=%s" % urllib.quote(t) for t in settings.BTDHT_TRACKERS)
        return "magnet:?xt=urn:btih:%s&db=%s&%s" % (
            self.hex_hash,
            urllib.quote(self.name.encode("utf-8")),
            trackers
        )

    @property
    def path(self):
        hex_hash = self.hex_hash
        return os.path.join(
            settings.BTDHT_TORRENTS_BASE_PATH,
            hex_hash[0],
            hex_hash[1],
            hex_hash[2],
            hex_hash[3],
            "%s.torrent" % hex_hash
        )

    @property
    def url(self):
        if os.path.isfile(self.path):
            return reverse(
                "btdht_search:download_torrent",
                args=[self.hex_hash, normalize_name(self.name)]
            )
        else:
            return None

    @property
    def info_url(self):
        return reverse(
            "btdht_search:info_torrent",
            args=[self.hex_hash, normalize_name(self.name)]
        )

    @property
    def info_url_noname(self):
        return reverse("btdht_search:info_torrent", args=[self.hex_hash])

    def social_share_urls(self):
        url = self.info_url
        url_noname = self.info_url_noname
        js_template = (
            "window.open(this.getAttribute('href'), '',  "
            "'menubar=no,toolbar=no,width=%s,height=%s,resizable=yes').focus();return false;"
        )
        return [
            ('twitter', {
                "url": "https://twitter.com/intent/tweet?text=%s&url=%s" % (
                    urlquote("Check out the #torrent I found on #BTDHTSearch !"),
                    urlquote(utils.absolute_url(self._request, url))
                ),
                "js": js_template % (500, 350),
                "txt": "Share on Twitter",
            }),
            ('facebook', {
                "url": "https://www.facebook.com/share.php?u=%s" % (
                    urlquote(utils.absolute_url(self._request, url)),
                ),
                "js": js_template % (570, 400),
                "txt": "Share on Facebook",
            }),
            ('google', {
                "url": "https://plus.google.com/share?url=%s" % (
                    urlquote(utils.absolute_url(self._request, url_noname)),
                ),
                "js": js_template % (500, 500),
                "txt": "Share on Google+",
                "icon": "google-plus",
            }),
            ('reddit', {
                "url": "https://www.reddit.com/submit?url=%s" % (
                    urlquote(utils.absolute_url(self._request, url)),
                ),
                "js": None,
                "txt": "Share on Reddit",
                "icon": "reddit-alien",
            }),
        ]

    @property
    def size_pp(self):
        return format_size(self.size)

    @property
    def created_pp(self):
        return format_date(self.created, timezone=self._timezone)

    @property
    def last_scrape_pp(self):
        if self.last_scrape is not None and self.last_scrape > 0:
            return format_date(self.last_scrape, timezone=self._timezone)
        else:
            "never"

    @property
    def last_scrape_delta(self):
        if self.last_scrape is not None:
            return time.time() - self.last_scrape

    @property
    def last_scrape_delta_pp(self):
        if self.last_scrape is not None:
            return utils.delta_pp(self.last_scrape)

    @property
    def dmca_deleted_pp(self):
        if self.dmca_deleted is not None:
            return format_date(self.dmca_deleted, timezone=self._timezone)

    @property
    def created_delta(self):
        return time.time() - self.created

    @property
    def created_delta_pp(self):
        return utils.delta_pp(self.created)

    def categories_pp(self):
        if self.categories:
            return ", ".join(self.categories)
        else:
            return ""

    @property
    def name_normalized(self):
        return normalize_name(self.name)
