from warnings import warn


class LegacyAPIWarning(FutureWarning):
    """A warning for aciClient Class that will be removed in a future release"""


def warn_legacy_api(old: str, replacement: str) -> None:
    msg = f"{old} is deprecated since aciClient 2.0.0 and will be removed in a future major release"
    warn(
        message=f"{msg}. Use {replacement} instead.",
        category=LegacyAPIWarning,
        stacklevel=3,
    )
