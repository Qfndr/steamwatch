#-*- coding: utf-8 -*-
'''
Main application module.
'''
import logging

from datetime import datetime
from pkg_resources import iter_entry_points

from steamwatch.exceptions import GameNotFoundError
from steamwatch.models import Game, Measure
from steamwatch.models import Database
from steamwatch import storeapi


log = logging.getLogger(__name__)


EP_SIGNALS = 'steamwatch.signals'
SIGNAL_ADDED = 'added'
SIGNAL_PRICE = 'price'
SIGNAL_THRESHOLD = 'threshold'


class Application(object):

    def __init__(self, db_path):
        self.db = Database(db_path)

    def add(self, appid, threshold=None):
        '''Add a Game to be watched.'''
        results = storeapi.appdetails(appid)
        try:
            data = results[appid]['data']
        except KeyError:
            raise GameNotFoundError('No game with appid {!r}.'.format(appid))
        game = Game(
            appid=appid,
            name = data.get('name'),
            threshold=threshold,
        )
        # TODO: what if the appid exists = sqlite3.IntegrityError?
        game.save(self.db)
        self._signal(SIGNAL_ADDED, gameid=game.id, appid=game.appid)

        self._store_measure(game, data)
        return game

    def get(self, appid):
        '''Get the :class:`Game` that is associated with the given ``appid``.

        :param str appid:
            The steam appid.
        :rtype:
            A :class:`Game` object.
        :raises:
            `NotFoundError` if we are not watching the given ``appid``.
        '''
        return Game.select(self.db).where('appid').equals(appid).one()

    def ls(self):
        '''List games'''
        return Game.select(self.db).many()

    def disable(self, appid):
        '''Stop watching the given ``appid``, but do not delete it.'''
        self._set_enabled(appid, False)

    def enable(self, appid):
        '''Resume watching a disabled ``appid``.'''
        self._set_enabled(appid, True)

    def _set_enabled(self, appid, enabled):
        enabled = bool(enabled)
        game = self.get(appid)
        if game.enabled != enabled:
            game.enabled = enabled
            game.save(self.db)

    def fetch_all(self):
        '''Update measures for all enabled Games.'''
        #TODO should be possible to call .equals(True)
        games = Game.select(self.db).where('enabled').equals('1').many()
        for game in games:
            self.fetch(game)

    def fetch(self, game):
        '''Update measures for the given Game.'''
        if not game.enabled:
            log.warning('{g!r} is disabled and will not be updated.'.format(
                g=Game))
            return

        appid = game.appid
        results = storeapi.appdetails(appid)
        data = results[appid]['data']
        self._store_measure(game, data)
        self._changes(game)

    def _store_measure(self, game, data):
        po = data.get('price_overview', {})
        currency = po.get('currency')
        price = po.get('final')
        baseprice = po.get('initial')
        discount = po.get('discount_percent')

        m = Measure(
            gameid=game.id,
            currency=currency,
            price=price / 100.0 if price else None,
            baseprice=baseprice / 100.0 if baseprice else None,
            discount=discount,
            metacritic=data.get('metacritic', {}).get('score'),
            datetaken=datetime.now()
        )
        m.save(self.db)

    def _changes(self, game):
        measures = Measure.select(self.db).where('gameid').equals(game.id).many()
        #TODO order_by and limit
        measures.sort(key=lambda x: x.datetaken)
        try:
            current = measures[0]
            previous = measures[1]
        except IndexError:
            # we have none or only one measure - no changes possible
            return

        if current.price != previous.price:
            self._signal(SIGNAL_PRICE,
                gameid=game.id,
                previous=previous.price,
                current=current.price
            )

            if game.threshold and current_measure.price <= game.threshold:
                self._signal(SIGNAL_THRESHOLD,
                    gameid=game.id,
                    threshold=game.threshold,
                    current=current.price
                )

    def report(self, game):
        '''List Measures for the given Game.'''
        measures = Measure.select(self.db).where('gameid').equals(game.id).many()
        #TODO order_by
        measures.sort(key=lambda x: x.datetaken)
        return measures

    def report_all(self):
        '''List Measures for all enabled Games.'''
        #TODO .equals(True)
        games = Game.select(self.db).where('enabled').equals('1').many()
        reports = {}
        for game in games:
            reports[game] = self.report(game)

        return reports

    def _signal(self, name, **data):
        log.debug('Emit {s!r}.'.format(s=name))
        for ep in iter_entry_points(EP_SIGNALS, name=name):
            try:
                hook = ep.load()
            except (ImportError, SyntaxError) as e:
                log.error('Failed to load entry point {ep!r}'.format(ep=ep))
                log.debug(e, exc_info=True)
                continue

            try:
                kwargs = {k: v for k, v in data.items()}
                hook(name, self, **kwargs)
                log.debug('Dispatched {n!r} to {ep!r}'.format(n=name, ep=ep))
            except Exception as e:
                log.error(('Failed to run entry point for {s!r}.'
                    ' Error was: {e!r}').format(s=name, e=e))
                log.debug(e, exc_info=True)


def log_signal(name, app, **kwargs):
    '''Default hook function for signals.'''
    s = ', '.join(['{k}={v!r}'.format(k=k, v=v) for k, v in kwargs.items()])
    log.debug('Signal {n!r} with {s!r}'.format(n=name, s=s))
