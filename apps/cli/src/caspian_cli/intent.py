from dataclasses import dataclass, field
from typing import Literal, Union


@dataclass(frozen=True)
class ChannelsAdd:
    channel: str
    via: Literal["hosted", "self-host"]
    display_name: str = ""
    bot_token: str = ""
    webhook_url: str = ""
    inbound: bool = True


@dataclass(frozen=True)
class ChannelsLs:
    pass


@dataclass(frozen=True)
class Call:
    """The only mutate/send intent. `id` is a catalog id (`post`, `telegram.send-photo`)."""

    id: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogList:
    pass


@dataclass(frozen=True)
class CatalogSearch:
    query: str


@dataclass(frozen=True)
class CatalogGet:
    id: str


@dataclass(frozen=True)
class ThreadsLs:
    channel: str = ""


@dataclass(frozen=True)
class ThreadsTail:
    thread_id: str = ""


Intent = Union[
    ChannelsAdd,
    ChannelsLs,
    Call,
    CatalogList,
    CatalogSearch,
    CatalogGet,
    ThreadsLs,
    ThreadsTail,
]
