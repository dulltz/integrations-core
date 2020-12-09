import re
from math import isnan
from typing import Any, Dict, List, Tuple

from six.moves import range, zip

from .errors import UnknownMetric, UnknownTags
from .metrics import METRIC_PREFIX, METRIC_TREE, METRICS

HISTOGRAM = re.compile(r'([P0-9.]+)\(([^,]+)')
PERCENTILE_SUFFIX = {
    'P0': '.0percentile',
    'P25': '.25percentile',
    'P50': '.50percentile',
    'P75': '.75percentile',
    'P90': '.90percentile',
    'P95': '.95percentile',
    'P99': '.99percentile',
    'P99.9': '.99_9percentile',
    'P100': '.100percentile',
}


def parse_metric(metric, metric_mapping=METRIC_TREE):
    # type: (str, Dict[str, Any]) -> Tuple[str, List[str], str]
    """Takes a metric formatted by Envoy and splits it into a unique
    metric name. Returns the unique metric name, a list of tags, and
    the name of the submission method.

    Example:
        'listener.0.0.0.0_80.downstream_cx_total' ->
        ('listener.downstream_cx_total', ['address:0.0.0.0_80'], 'count')
    """
    metric_parts = []
    tag_names = []
    tag_values = []
    tag_value_builder = []
    unknown_tags = []
    tags_to_build = 0
    minimum_tag_length = 0

    # From the split metric name, any part that is not in the mapping it will become part of the tag value
    for metric_part in metric.split('.'):
        if metric_part in metric_mapping and tags_to_build >= minimum_tag_length:
            # Rebuild any built up tags whenever we encounter a known metric part.
            if tag_value_builder:
                # Edge case where we hit a known metric part after a sequence of all unknown parts
                if '|_tags_|' not in metric_mapping:
                    raise UnknownMetric

                tags = next(
                    (mapped_tags for mapped_tags in metric_mapping['|_tags_|'] if tags_to_build >= len(mapped_tags)),
                    tuple(),
                )
                constructed_tag_values = construct_tag_values(tag_value_builder, len(tags))
                # Once the builder has been used, clear its contents.
                tag_value_builder = []

                if tags:
                    tag_names.extend(tags)
                    tag_values.extend(constructed_tag_values)
                else:
                    unknown_tags.extend(constructed_tag_values)

                tags_to_build = 0

            metric_parts.append(metric_part)
            metric_mapping = metric_mapping[metric_part]
            minimum_tag_length = len(metric_mapping['|_tags_|'][-1])
        else:
            tag_value_builder.append(metric_part)
            tags_to_build += 1

    metric = '.'.join(metric_parts)
    if metric not in METRICS:
        raise UnknownMetric

    # Rebuild any trailing tags
    if tag_value_builder:
        tags = next(
            (mapped_tags for mapped_tags in metric_mapping['|_tags_|'] if tags_to_build >= len(mapped_tags)), tuple()
        )
        constructed_tag_values = construct_tag_values(tag_value_builder, len(tags))

        if tags:
            tag_names.extend(tags)
            tag_values.extend(constructed_tag_values)
        else:
            unknown_tags.extend(constructed_tag_values)

    if unknown_tags:
        raise UnknownTags('{}'.format('|||'.join(unknown_tags)))

    tags = ['{}:{}'.format(tag_name, tag_value) for tag_name, tag_value in zip(tag_names, tag_values)]

    return METRIC_PREFIX + metric, tags, METRICS[metric]['method']


def construct_tag_values(tag_builder, num_tags):
    # type: (List[str], int) -> List[str]

    # First fill in all trailing slots with one tag.
    tags = [tag_builder.pop() for _ in range(num_tags - 1)]

    # Merge any excess tag parts.
    if tag_builder:
        tags.append('.'.join(tag_builder))

    # Return an iterator in the original order.
    return reversed(tags)


def parse_histogram(metric, histogram):
    """Iterates over histogram data, yielding metric-value pairs."""
    for match in HISTOGRAM.finditer(histogram):
        percentile, value = match.groups()
        value = float(value)

        if not isnan(value):
            try:
                yield metric + PERCENTILE_SUFFIX[percentile], value

            # In case Envoy adds more
            except KeyError:
                yield '{}.{}percentile'.format(metric, percentile[1:].replace('.', '_')), value
