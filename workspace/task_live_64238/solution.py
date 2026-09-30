# AtlasAeon Automated Candidate Patch
def query(*args, **kwargs):
    """Route decorator implementation for app.query."""
    def decorator(f):
        f._query_route = True
        return f
    return decorator
