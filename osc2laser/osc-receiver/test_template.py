# Feeds every control of an open-stage-control session through the receiver's
# OSC handler and renderer, so template <-> receiver drift shows up as a failure.
# Run: .venv/bin/pytest -q    (other session: TEMPLATE=path/to.json .venv/bin/pytest -q)
import configparser
import json
import os
import pathlib

import pytest

import global_data

HERE = pathlib.Path(__file__).parent
TEMPLATE = pathlib.Path(os.environ.get(
    'TEMPLATE', HERE / '../osc-senders/open-stage-control/pavillion-template.json'))

global_data.config = configparser.ConfigParser()
global_data.config.read(HERE / 'config_laser1.txt')
global_data.scan_rate = global_data.config['laser_output']['scan_rate']

from osc_input import handle_osc_message  # noqa: E402  (needs config loaded first)
from optimizer import get_optimized_point_list  # noqa: E402

RECEIVER_SOURCE = ''.join(p.read_text() for p in HERE.glob('*.py') if p.name != 'test_template.py')


def widgets(node):
    if isinstance(node, dict):
        if node.get('address', '').startswith('/') and node.get('type') not in ('text', 'button', 'root', 'panel', 'tab', 'folder', 'modal'):
            yield node
        for v in node.values():
            yield from widgets(v)
    elif isinstance(node, list):
        for v in node:
            yield from widgets(v)


def values_for(w):
    """Every value the widget can send: dropdown entries, xy corners, range ends and default."""
    t = w['type']
    if t == 'dropdown':
        return [[v] for v in w['values'].values()]
    if t == 'xy':
        rx, ry = w['rangeX'], w['rangeY']
        return [[x, y] for x in (rx['min'], rx['max']) for y in (ry['min'], ry['max'])]
    if t == 'toggle':
        return [[0], [1]]
    r = w.get('range') or {'min': 0, 'max': 1}
    vals = [r['min'], r['max']]
    if w.get('default') not in (None, ''):
        vals.append(float(w['default']))
    return [[v] for v in vals]


WIDGETS = list(widgets(json.loads(TEMPLATE.read_text())))


@pytest.fixture(autouse=True)
def circle_visible():
    global_data.parameters.clear()
    handle_osc_message('/laserobject', 5)


@pytest.mark.parametrize('w', WIDGETS, ids=lambda w: w['address'])
def test_widget_is_handled(w):
    addr = w['address']
    if addr == '/globals/scan_rate':
        # ponytail: skip the render check, scan rate only feeds the DAC
        for args in values_for(w):
            handle_osc_message(addr, *args)
            assert global_data.scan_rate == int(args[0])
        return

    for args in values_for(w):
        handle_osc_message(addr, *args)

        if addr == '/laserobject':
            assert int(args[0]) < len(global_data.NOTE_LASEROBJECT_MAPPING), f"receiver has no laser object {args[0]}"
            assert type(global_data.visible_laser_objects[0]).__name__ == \
                type(global_data.NOTE_LASEROBJECT_MAPPING[int(args[0])]).__name__, \
                f'receiver has no laser object {args[0]}'
        elif addr.startswith('/effect/'):
            names = [e.name for e in global_data.visible_laser_objects[0].effects]
            assert 'UNKNOWN_EFFECT' not in names, f'receiver ignores {addr}'
        elif addr.startswith('/parameters/'):
            name = addr.split('/')[2]
            assert f"'{name}'" in RECEIVER_SOURCE, f'no laser object reads parameter {name}'
        else:
            pytest.fail(f'receiver has no handler for {addr}')

        get_optimized_point_list()  # must render without raising
