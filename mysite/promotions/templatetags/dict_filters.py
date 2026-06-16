from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key."""
    if dictionary is None:
        return []
    return dictionary.get(key, [])


@register.filter
def get_attr(obj, attr):
    """Get an attribute from an object dynamically."""
    return getattr(obj, attr, '')
