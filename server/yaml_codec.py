"""Safe YAML loading for user configs, including legacy numpy-tagged exports."""
import yaml


class LenientSafeLoader(yaml.SafeLoader):
    """SafeLoader that discards unknown tagged values instead of executing them."""


def _unknown_tag(loader, node):
    # Legacy result exports used ``!!python/object/apply`` for numpy scalars.
    # The importer never needs those cached values; returning None preserves
    # the surrounding mission/config structure without executing YAML objects.
    return None


LenientSafeLoader.add_constructor(None, _unknown_tag)


def load_config(text):
    return yaml.load(text, Loader=LenientSafeLoader)
