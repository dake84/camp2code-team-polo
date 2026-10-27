"""Minimale Attrappe fuer dash, dash_bootstrap_components und plotly.

Erlaubt hardware- und browserfreie Tests der Callback-Registrierung und
einzelner Callbacks, auch wenn Dash nicht installiert ist. Es wird nur die
Teilmenge der API nachgebildet, die Camp2CodeDashboard verwendet.
"""
import types
from collections import namedtuple


class Component:
    def __init__(self, *args, **kwargs):
        self.type = type(self).__name__
        self.children = args[0] if args else kwargs.pop('children', None)
        self.id = kwargs.get('id')
        self.props = kwargs

    def walk(self):
        yield self
        children = self.children
        if isinstance(children, (list, tuple)):
            for child in children:
                if isinstance(child, Component):
                    yield from child.walk()
        elif isinstance(children, Component):
            yield from children.walk()


def _component_module(name, extra=None):
    module = types.ModuleType(name)
    cache = {}

    def __getattr__(attr):
        if attr.startswith('__'):
            raise AttributeError(attr)
        if attr not in cache:
            cache[attr] = type(attr, (Component,), {})
        return cache[attr]

    module.__getattr__ = __getattr__
    for key, value in (extra or {}).items():
        setattr(module, key, value)
    return module


Dependency = namedtuple('Dependency', 'component_id component_property')


class Input(Dependency):
    pass


class Output(Dependency):
    pass


class State(Dependency):
    pass


class _NoUpdate:
    def __repr__(self):
        return 'no_update'


no_update = _NoUpdate()


class CallbackContext:
    def __init__(self):
        self.triggered_id = None
        self.triggered = []

    def set(self, component_id, prop='n_clicks'):
        self.triggered_id = component_id
        self.triggered = [{'prop_id': f'{component_id}.{prop}', 'value': None}] if component_id else []


ctx = CallbackContext()


class Callback:
    def __init__(self, func, deps, kwargs):
        self.func = func
        self.outputs = [d for d in deps if isinstance(d, Output)]
        self.inputs = [d for d in deps if isinstance(d, Input)]
        self.states = [d for d in deps if isinstance(d, State)]
        self.kwargs = kwargs

    def __call__(self, *args):
        return self.func(*args)


class Dash:
    def __init__(self, name=None, **kwargs):
        self.kwargs = kwargs
        self.layout = None
        self.callbacks = {}

    def callback(self, *deps, **kwargs):
        def decorator(func):
            self.callbacks[func.__name__] = Callback(func, deps, kwargs)
            return func
        return decorator

    def run(self, *args, **kwargs):  # pragma: no cover - nie im Test gestartet
        raise RuntimeError('Attrappe: kein Server')


class Figure:
    def __init__(self):
        self.data = []
        self.layout = {}
        self.vlines = []

    def add_bar(self, **kwargs):
        self.data.append(('bar', kwargs))

    def add_scatter(self, **kwargs):
        self.data.append(('scatter', kwargs))

    def add_vline(self, **kwargs):
        self.vlines.append(kwargs)

    def update_layout(self, **kwargs):
        self.layout.update(kwargs)


def modules():
    dash = types.ModuleType('dash')
    html = _component_module('dash.html')
    dcc = _component_module('dash.dcc', {'send_file': lambda path: {'path': path}})
    dash.Dash, dash.Input, dash.Output, dash.State = Dash, Input, Output, State
    dash.ctx, dash.no_update, dash.html, dash.dcc = ctx, no_update, html, dcc
    dbc = _component_module('dash_bootstrap_components', {'themes': types.SimpleNamespace(DARKLY='darkly')})
    plotly = types.ModuleType('plotly')
    go = types.ModuleType('plotly.graph_objects')
    go.Figure = Figure
    plotly.graph_objects = go
    return {'dash': dash, 'dash.html': html, 'dash.dcc': dcc, 'dash_bootstrap_components': dbc,
            'plotly': plotly, 'plotly.graph_objects': go}
