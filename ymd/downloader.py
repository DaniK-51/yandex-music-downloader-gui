import itertools
import logging
import re
import threading
import time
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse

from yandex_music import Album, Client, Track

from ymd import core

logger = logging.getLogger("yandex-music-downloader")

TRACK_RE = re.compile(r"track/(\d+)")
ALBUM_RE = re.compile(r"album/(\d+)$")
ARTIST_RE = re.compile(r"artist/(\d+)$")
PLAYLIST_RE = re.compile(r"playlists/(.+)$")

FETCH_PAGE_SIZE = 10


class DownloadState(Enum):
    IDLE = auto()
    DOWNLOADING = auto()
    PAUSED = auto()
    STOPPING = auto()


@dataclass
class DownloadProgress:
    current: int
    total: Optional[int]
    track_title: str
    track_artist: str
    status: str  # "downloading" | "done" | "skipped" | "error"
    file_path: str
    codec: str
    bitrate: int
    state: DownloadState
    errors: int
    queue_remaining: int


def get_artists(track: Track) -> str:
    return ", ".join(a["name"] for a in track.artists)


def parse_url(url: str) -> tuple[str, str]:
    """Parse Yandex Music URL, return (entity_type, entity_id)."""
    parsed = urlparse(url)
    path = parsed.path
    if match := ARTIST_RE.search(path):
        return "artist", match.group(1)
    elif match := ALBUM_RE.search(path):
        return "album", match.group(1)
    elif match := TRACK_RE.search(path):
        return "track", match.group(1)
    elif match := PLAYLIST_RE.search(path):
        return "playlist", match.group(1)
    else:
        raise ValueError(f"Неверный формат URL: {url}")


@dataclass
class DownloadOptions:
    token: str
    quality: int = 0
    output_dir: Path = Path(".")
    path_pattern: Path = core.DEFAULT_PATH_PATTERN
    unsafe_path: bool = False
    skip_existing: bool = False
    embed_cover: bool = False
    cover_resolution: int = core.DEFAULT_COVER_RESOLUTION
    lyrics_format: core.LyricsFormat = core.LyricsFormat.NONE
    delay: int = 0
    stick_to_artist: bool = False
    only_music: bool = False
    compatibility_level: int = 1
    timeout: int = 20
    tries: int = 20
    retry_delay: int = 5


class Downloader:
    def __init__(self, options: DownloadOptions):
        self.options = options
        self.state = DownloadState.IDLE
        self.errors = 0
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self._stopped = False
        self._client: Optional[Client] = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = core.init_client(
                token=self.options.token,
                timeout=self.options.timeout,
                max_try_count=self.options.tries,
                retry_delay=self.options.retry_delay,
            )
        return self._client

    def pause(self):
        if self.state == DownloadState.DOWNLOADING:
            self.state = DownloadState.PAUSED
            self._pause_event.clear()

    def resume(self):
        if self.state == DownloadState.PAUSED:
            self.state = DownloadState.DOWNLOADING
            self._pause_event.set()

    def stop(self):
        self._stopped = True
        self._pause_event.set()  # unblock if paused
        self.state = DownloadState.STOPPING

    def reset(self):
        self.state = DownloadState.IDLE
        self.errors = 0
        self._pause_event.set()
        self._stopped = False

    def _resolve_tracks(
        self, url: str
    ) -> tuple[Iterable[Track], Optional[int]]:
        """Resolve URL to an iterable of Tracks and optional total count."""
        entity_type, entity_id = parse_url(url)
        client = self.client

        result_tracks: Iterable[Track]
        total_track_count: Optional[int] = None

        if entity_type == "artist":

            def filter_album(album: Album) -> bool:
                title = album.title
                if album.id is None or not album.available:
                    logger.info(f'Альбом "{title}" не доступен для скачивания')
                elif self.options.only_music and album.meta_type != "music":
                    logger.info(
                        f'Альбом "{title}" пропущен т.к. не является музыкальным'
                    )
                elif (
                    self.options.stick_to_artist
                    and album.artists[0].id != int(entity_id)
                ):
                    logger.info(
                        f'Альбом "{title}" пропущен из-за флага stick-to-artist'
                    )
                else:
                    return True
                return False

            def albums_id_gen() -> Generator[int, None, None]:
                nonlocal total_track_count
                has_next = True
                page = 0
                while has_next:
                    if albums_info := client.artists_direct_albums(
                        entity_id, page
                    ):
                        for album in albums_info.albums:
                            if filter_album(album):
                                assert album.id
                                yield album.id
                            else:
                                if (
                                    track_count := album.track_count
                                ) and total_track_count is not None:
                                    total_track_count -= track_count
                    else:
                        break
                    if pager := albums_info.pager:
                        page = pager.page + 1
                        has_next = pager.per_page * page < pager.total
                    else:
                        break

            def album_tracks_gen(
                album_ids: Iterable[Union[int, str]],
            ) -> Generator[Track, None, None]:
                for album_id in album_ids:
                    if full_album := client.albums_with_tracks(album_id):
                        if volumes := full_album.volumes:
                            yield from itertools.chain.from_iterable(volumes)

            result_tracks = album_tracks_gen(albums_id_gen())
            artist = client.artists(entity_id)[0]
            if counts := artist.counts:
                total_track_count = counts.tracks

        elif entity_type == "album":

            def album_tracks_gen_single() -> Generator[Track, None, None]:
                if full_album := client.albums_with_tracks(entity_id):
                    if volumes := full_album.volumes:
                        yield from itertools.chain.from_iterable(volumes)

            result_tracks = album_tracks_gen_single()
            if album := client.albums_with_tracks(entity_id):
                total_track_count = album.track_count

        elif entity_type == "track":
            result_tracks = client.tracks(entity_id)
            total_track_count = 1

        elif entity_type == "playlist":
            playlist = client.playlist(entity_id)
            assert playlist is not None
            total_track_count = playlist.track_count

            def playlist_tracks_gen() -> Generator[Track, None, None]:
                tracks = playlist.fetch_tracks()
                for i in range(0, len(tracks), FETCH_PAGE_SIZE):
                    yield from client.tracks(
                        [track.id for track in tracks[i : i + FETCH_PAGE_SIZE]]
                    )

            result_tracks = playlist_tracks_gen()
        else:
            raise ValueError(f"Неизвестный тип сущности: {entity_type}")

        return result_tracks, total_track_count

    def download(
        self,
        urls: list[str],
        on_progress: Callable[[DownloadProgress], None],
    ) -> None:
        """Download tracks from one or more URLs with progress callbacks."""
        self.reset()
        self.state = DownloadState.DOWNLOADING
        covers_cache: dict[int, core.AlbumCover] = {}
        queue_remaining = len(urls) - 1

        for url_index, url in enumerate(urls):
            if self._stopped:
                break

            queue_remaining = len(urls) - url_index - 1

            try:
                result_tracks, total_track_count = self._resolve_tracks(url)
            except Exception as e:
                self.errors += 1
                on_progress(
                    DownloadProgress(
                        current=0,
                        total=None,
                        track_title="",
                        track_artist="",
                        status="error",
                        file_path=url,
                        codec="",
                        bitrate=0,
                        state=self.state,
                        errors=self.errors,
                        queue_remaining=queue_remaining,
                    )
                )
                logger.error(f"Ошибка при разрешении URL {url}: {e}")
                continue

            track_counter = 0
            for track in result_tracks:
                # Check pause/stop
                self._pause_event.wait()
                if self._stopped:
                    break

                if total_track_count:
                    track_counter += 1

                if not track.available:
                    self.errors += 1
                    artist_name = get_artists(track) if track.artists else "?"
                    title = track.title or "?"
                    on_progress(
                        DownloadProgress(
                            current=track_counter,
                            total=total_track_count,
                            track_title=title,
                            track_artist=artist_name,
                            status="error",
                            file_path="",
                            codec="",
                            bitrate=0,
                            state=self.state,
                            errors=self.errors,
                            queue_remaining=queue_remaining,
                        )
                    )
                    continue

                save_path = self.options.output_dir / core.prepare_base_path(
                    self.options.path_pattern,
                    track,
                    self.options.unsafe_path,
                )

                if self.options.skip_existing:
                    if any(
                        Path(str(save_path) + s).is_file()
                        for s in core.AUDIO_FILE_SUFFIXES
                    ):
                        on_progress(
                            DownloadProgress(
                                current=track_counter,
                                total=total_track_count,
                                track_title=track.title or "",
                                track_artist=get_artists(track),
                                status="skipped",
                                file_path=str(save_path),
                                codec="",
                                bitrate=0,
                                state=self.state,
                                errors=self.errors,
                                queue_remaining=queue_remaining,
                            )
                        )
                        continue

                save_dir = save_path.parent
                if not save_dir.is_dir():
                    save_dir.mkdir(parents=True)

                try:
                    downloadable = core.to_downloadable_track(
                        track, self.options.quality, save_path
                    )
                except Exception as e:
                    self.errors += 1
                    logger.error(
                        f"Ошибка подготовки трека {track.title}: {e}"
                    )
                    on_progress(
                        DownloadProgress(
                            current=track_counter,
                            total=total_track_count,
                            track_title=track.title or "",
                            track_artist=get_artists(track),
                            status="error",
                            file_path="",
                            codec="",
                            bitrate=0,
                            state=self.state,
                            errors=self.errors,
                            queue_remaining=queue_remaining,
                        )
                    )
                    continue

                bitrate = downloadable.download_info.bitrate
                codec_name = downloadable.download_info.file_format.codec.name

                on_progress(
                    DownloadProgress(
                        current=track_counter,
                        total=total_track_count,
                        track_title=track.title or "",
                        track_artist=get_artists(track),
                        status="downloading",
                        file_path=str(downloadable.path),
                        codec=codec_name,
                        bitrate=bitrate,
                        state=self.state,
                        errors=self.errors,
                        queue_remaining=queue_remaining,
                    )
                )

                try:
                    core.download_track(
                        track_info=downloadable,
                        lyrics_format=self.options.lyrics_format,
                        embed_cover=self.options.embed_cover,
                        cover_resolution=self.options.cover_resolution,
                        covers_cache=covers_cache,
                        compatibility_level=self.options.compatibility_level,
                    )
                except Exception as e:
                    self.errors += 1
                    logger.error(
                        f"Ошибка загрузки трека {track.title}: {e}"
                    )
                    on_progress(
                        DownloadProgress(
                            current=track_counter,
                            total=total_track_count,
                            track_title=track.title or "",
                            track_artist=get_artists(track),
                            status="error",
                            file_path=str(downloadable.path),
                            codec=codec_name,
                            bitrate=bitrate,
                            state=self.state,
                            errors=self.errors,
                            queue_remaining=queue_remaining,
                        )
                    )
                    continue

                on_progress(
                    DownloadProgress(
                        current=track_counter,
                        total=total_track_count,
                        track_title=track.title or "",
                        track_artist=get_artists(track),
                        status="done",
                        file_path=str(downloadable.path),
                        codec=codec_name,
                        bitrate=bitrate,
                        state=self.state,
                        errors=self.errors,
                        queue_remaining=queue_remaining,
                    )
                )

                if self.options.delay > 0:
                    time.sleep(self.options.delay)

        self.state = DownloadState.IDLE
