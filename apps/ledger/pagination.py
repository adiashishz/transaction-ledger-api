"""Page/limit pagination shared by the list endpoints."""

import math

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


def paginate(queryset, page, limit):
    """Return one page of an ordered queryset and the metadata a client needs to page through it.

    A page past the end returns no items instead of an error, and skips the query for them.
    """
    total_records = queryset.count()
    total_pages = math.ceil(total_records / limit)
    items = list(queryset[(page - 1) * limit : page * limit]) if page <= total_pages else []
    return items, {"page": page, "limit": limit, "total_records": total_records, "total_pages": total_pages}
