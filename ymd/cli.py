#!/bin/python3
import argparse
import logging
import sys
from argparse import ArgumentTypeError
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from ymd import core
from ymd.downloader import DownloadOptions, DownloadProgress, Downloader

DEFAULT_DELAY = 0

logger = logging.getLogger("yandex-music-downloader")


def show_default(text: Optional[str] = None) -> str:
    default = "по умолчанию: %(default)s"
    if text is None:
        return default
    return f"{text} ({default})"


def checked_int_arg(
    min_value: int, max_value: Optional[int] = None
) -> Callable[[str], int]:
    def func(astr: str) -> int:
        aint = int(astr)
        if aint >= min_value and (max_value is None or aint <= max_value):
            return aint
        error_text = f"Значение должен быть >= {min_value}"
        if max_value is not None:
            error_text += f" и <= {max_value}"
        raise ArgumentTypeError(error_text)

    return func


def cover_resolution_arg(astr: str) -> int:
    if astr == "original":
        return -1
    return checked_int_arg(100)(astr)


def lyrics_format_arg(astr: str) -> core.LyricsFormat:
    try:
        return core.LyricsFormat(astr)
    except ValueError:
        raise ArgumentTypeError(
            f"Допустимые значения: {','.join(core.LyricsFormat)}"
        )


def cli_progress_callback(progress: DownloadProgress):
    status_prefix = ""
    if progress.total:
        status_prefix = f"[{progress.current}/{progress.total}] "

    if progress.status == "downloading":
        format_info = f"[{progress.codec}"
        if progress.bitrate > 0:
            format_info += f" {progress.bitrate}kbps"
        format_info += "]"
        print(f"{status_prefix}{format_info} Загружается {progress.file_path}")
    elif progress.status == "done":
        pass  # already printed "downloading"
    elif progress.status == "skipped":
        print(f"{status_prefix}Пропущен {progress.file_path}")
    elif progress.status == "error":
        if progress.track_title:
            print(
                f"{status_prefix}Ошибка: {progress.track_artist} - {progress.track_title}"
            )
        else:
            print(f"{status_prefix}Ошибка: {progress.file_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Загрузчик музыки с сервиса Яндекс.Музыка",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    common_group = parser.add_argument_group("Общие параметры")
    common_group.add_argument(
        "--quality",
        metavar="<Качество>",
        default=0,
        type=checked_int_arg(0, 2),
        help="Качество трека:\n0 - Низкое (AAC 64kbps)\n1 - Оптимальное (AAC 192kbps)\n2 - Лучшее (FLAC)\n(по умолчанию: %(default)s)",
    )
    common_group.add_argument(
        "--skip-existing",
        action="store_true",
        help="Пропускать уже загруженные треки",
    )
    common_group.add_argument(
        "--lyrics-format",
        type=lyrics_format_arg,
        default=core.LyricsFormat.NONE,
        help=show_default("Формат текста песни"),
        choices=core.LyricsFormat,
    )
    common_group.add_argument(
        "--add-lyrics", action="store_true", help=argparse.SUPPRESS
    )
    common_group.add_argument(
        "--embed-cover",
        action="store_true",
        help="Встраивать обложку в аудиофайл",
    )
    common_group.add_argument(
        "--cover-resolution",
        default=core.DEFAULT_COVER_RESOLUTION,
        metavar="<Разрешение обложки>",
        type=cover_resolution_arg,
        help=show_default(
            'Разрешение обложки (в пикселях). Передайте "original" для загрузки в оригинальном (наилучшем) разрешении'
        ),
    )
    common_group.add_argument(
        "--delay",
        default=DEFAULT_DELAY,
        metavar="<Задержка>",
        type=checked_int_arg(0),
        help=show_default("Задержка между запросами, в секундах"),
    )
    common_group.add_argument(
        "--stick-to-artist",
        action="store_true",
        help="Загружать альбомы, созданные только данным исполнителем",
    )
    common_group.add_argument(
        "--only-music",
        action="store_true",
        help="Загружать только музыкальные альбомы (пропускать подкасты и аудиокниги)",
    )
    common_group.add_argument(
        "--compatibility-level",
        metavar="<Уровень совместимости>",
        default=1,
        type=checked_int_arg(
            core.MIN_COMPATIBILITY_LEVEL, core.MAX_COMPATIBILITY_LEVEL
        ),
        help=show_default(
            f"Уровень совместимости, от {core.MIN_COMPATIBILITY_LEVEL} до {core.MAX_COMPATIBILITY_LEVEL}. См. README для подробного описания"
        ),
    )

    network_group = parser.add_argument_group("Сетевые параметры")
    network_group.add_argument(
        "--timeout",
        metavar="<Время ожидания>",
        default=20,
        type=checked_int_arg(1),
        help=show_default(
            "Время ожидания ответа от сервера, в секундах. Увеличьте если возникают сетевые ошибки"
        ),
    )
    network_group.add_argument(
        "--tries",
        metavar="<Количество попыток>",
        default=20,
        type=checked_int_arg(0),
        help=show_default(
            "Количество попыток при возникновении сетевых ошибок. 0 - бесконечное количество попыток"
        ),
    )
    network_group.add_argument(
        "--retry-delay",
        metavar="<Задержка>",
        default=5,
        type=checked_int_arg(0),
        help=show_default(
            "Задержка между повторными запросами при сетевых ошибках"
        ),
    )
    common_group.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)

    id_group_meta = parser.add_argument_group("ID")
    id_group = id_group_meta.add_mutually_exclusive_group()
    id_group.add_argument("--artist-id", metavar="<ID исполнителя>")
    id_group.add_argument("--album-id", metavar="<ID альбома>")
    id_group.add_argument("--track-id", metavar="<ID трека>")
    id_group.add_argument(
        "--playlist-id",
        metavar="<владелец плейлиста>/<тип плейлиста>",
    )
    id_group.add_argument(
        "-u", "--url", help="URL исполнителя/альбома/трека/плейлиста"
    )

    path_group = parser.add_argument_group("Указание пути")
    path_group.add_argument(
        "--unsafe-path",
        action="store_true",
        help="Не очищать путь от недопустимых символов",
    )
    path_group.add_argument(
        "--dir",
        default=".",
        metavar="<Папка>",
        help=show_default("Папка для загрузки музыки"),
        type=Path,
    )
    path_group.add_argument(
        "--path-pattern",
        default=core.DEFAULT_PATH_PATTERN,
        metavar="<Паттерн>",
        type=Path,
        help=show_default(
            "Поддерживает следующие заполнители:"
            " #number, #track-artist, #album-artist, #title,"
            " #album, #year, #artist-id, #album-id, #track-id, #number-padded"
        ),
    )

    auth_group = parser.add_argument_group("Авторизация")
    auth_group.add_argument(
        "--token",
        metavar="<Токен>",
        help="Токен для авторизации. См. README для способов получения",
    )

    gui_group = parser.add_argument_group("GUI")
    gui_group.add_argument(
        "--gui", action="store_true", help="Запустить графический интерфейс (PyQt5)"
    )
    gui_group.add_argument(
        "--web", action="store_true", help="Запустить веб-интерфейс (Flask)"
    )

    return parser


def build_url(args) -> str:
    """Build URL from legacy --artist-id/--album-id/etc. arguments."""
    if args.url:
        return args.url
    if args.artist_id:
        return f"https://music.yandex.ru/artist/{args.artist_id}"
    if args.album_id:
        return f"https://music.yandex.ru/album/{args.album_id}"
    if args.track_id:
        return f"https://music.yandex.ru/track/{args.track_id}"
    if args.playlist_id:
        return f"https://music.yandex.ru/playlists/{args.playlist_id}"
    return ""


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Handle --gui flag
    if args.gui:
        from ymd.gui_qt import main as gui_main

        return gui_main()

    # Handle --web flag
    if args.web:
        from ymd.gui_web import main as web_main

        return web_main()

    logging.basicConfig(
        format="%(asctime)s |%(levelname)s| %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if args.debug else logging.ERROR,
    )

    if args.add_lyrics:
        print(
            "Аргумент --add-lyrics устарел и будет удален в будущем. Используйте --lyrics-format"
        )
        args.lyrics_format = core.LyricsFormat.TEXT

    url = build_url(args)
    if not url:
        parser.error(
            "Укажите URL (--url) или один из ID (--artist-id, --album-id, --track-id, --playlist-id)"
        )

    if not args.token:
        parser.error("Укажите токен авторизации (--token)")

    options = DownloadOptions(
        token=args.token,
        quality=args.quality,
        output_dir=Path(args.dir),
        path_pattern=Path(args.path_pattern),
        unsafe_path=args.unsafe_path,
        skip_existing=args.skip_existing,
        embed_cover=args.embed_cover,
        cover_resolution=args.cover_resolution,
        lyrics_format=args.lyrics_format,
        delay=args.delay,
        stick_to_artist=args.stick_to_artist,
        only_music=args.only_music,
        compatibility_level=args.compatibility_level,
        timeout=args.timeout,
        tries=args.tries,
        retry_delay=args.retry_delay,
    )

    downloader = Downloader(options)
    downloader.download([url], cli_progress_callback)

    return 1 if downloader.errors > 0 else 0
