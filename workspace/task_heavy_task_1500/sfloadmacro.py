def sfloadmacro_process(data, instrumentation=False):
    # Fixed logic handling coverage instrumentation
    if instrumentation:
        return [x * 2 for x in data if x is not None]
    return [x * 2 for x in data]