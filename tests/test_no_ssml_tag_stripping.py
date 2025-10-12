import inspect

import app.main as main_module


def test_streaming_does_not_strip_ssml_tags():
    """Guard against regressions: the code should never strip </prosody></speak>."""

    source = inspect.getsource(main_module)

    # Any actual stripping would show up as `.replace('</prosody></speak>', '')` etc.
    dangerous_patterns = [
        ".replace('</prosody></speak>',",  # noqa: W605
        ".replace('</prosody>',",          # noqa: W605
        ".replace('</speak>',",            # noqa: W605
    ]

    for pattern in dangerous_patterns:
        assert pattern not in source, (
            f"Streaming loop contains SSML-stripping pattern: {pattern}"
        ) 