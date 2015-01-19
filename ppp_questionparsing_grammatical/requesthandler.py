"""Request handler of the module."""

import json
import hashlib
import logging
import functools
import jsonrpclib

# Import pylibmc if possible; import memcache otherwise.
# pylibmc is is more strict (ie. detects and raises errors instead
# of just ignoring them), but is not compatible with Pypy.
try:
    import pylibmc as memcache
except ImportError:
    try:
        import memcache
    except ImportError:
        raise ImportError('Neither pylibmc or python3-memcached is installed')

from ppp_datamodel import Sentence, Resource
from ppp_datamodel.communication import TraceItem, Response
from ppp_libmodule.exceptions import ClientError

from .config import Config
from . import computeTree, simplify, normalize, QuotationHandler, QuotationError

def connect_memcached():
    mc = memcache.Client(Config().memcached_servers)
    return mc

class StanfordNLP:
    def __init__(self, url):
        self.server = jsonrpclib.Server(url)

    def parse(self, text):
        return json.loads(self.server.parse(text))
stanfordnlp = StanfordNLP(Config().corenlp_server)

def _parse(sentence):
    handler = QuotationHandler()
    nonAmbiguousSentence = handler.pull(sentence)
    result = stanfordnlp.parse(nonAmbiguousSentence)
    tree = computeTree(result['sentences'][0])
    handler.push(tree)
    qw = simplify(tree)
    return normalize(tree)

def parse(sentence):
    """Perform a query to all configured APIs and concatenates all
    results into a single list.
    Also handles caching."""
    mc = connect_memcached()

    # Construct a key suitable for memcached (ie. a string of less than
    # 250 bytes) with a salt (to prevent attacks by hash collision)
    salt = Config().memcached_salt
    key = hashlib.md5((salt + sentence).encode()).hexdigest()
    key = 'ppp-qp-grammatical-%s' + key

    # Get the cached value, if any
    r = mc.get(key)
    if not r:
        # If there is no cached value, query HAL and add the result to
        # the cache.
        r = _parse(sentence)
        mc.set(key, r, time=Config().memcached_timeout)
    return r

class RequestHandler:
    __slots__ = ('request',)
    def __init__(self, request):
        self.request = request

    def answer(self):
        if not isinstance(self.request.tree, Sentence):
            return []
        sentence = self.request.tree.value
        try:
            tree = parse(sentence)
        except QuotationError: # no logging, the error is between the chair and the keyboard
            return []
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logging.warning(e)
            return []
        if isinstance(tree,Resource):
            return []
        meas = {'accuracy': 0.5, 'relevance': 0.5}
        trace = self.request.trace + [TraceItem('QuestionParsing-Grammatical', tree, meas)]
        response = Response('en', tree, meas, trace)
        return [response]
