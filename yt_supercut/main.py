import json
from pathlib import Path
import subprocess
import typer
import threading
import concurrent.futures
from tqdm import tqdm
from tabulate import tabulate
from . import db, utils

cli = typer.Typer()
lock = threading.Lock()

# create tables
db.init()


@cli.command()
def index(
    url: str,
    lang: str = "es",
    verbose: bool = False,
    max_threads: int = None,
):
    print(f"Fetching video urls from {url}...")
    video_ids = list(utils.get_video_ids(url, verbose=verbose))
    new_ids = list(db.filter_existing_video_ids(video_ids, lang))

    # download and index subs in parallel
    with concurrent.futures.ThreadPoolExecutor(max_threads) as executor:
        tasks = (
            executor.submit(
                utils.download_and_process_subtitles,
                video_id,
                lang,
                lock=lock,
                verbose=verbose,
            ) 
            for video_id in new_ids
        )
        try:
            for future in tqdm(
                concurrent.futures.as_completed(tasks), 
                desc="Downloading subtitles",
                total=len(new_ids),
            ):
                try:
                    future.result()
                except Exception as e:
                    typer.echo(f"Error downloading subtitles: {e}")
        except KeyboardInterrupt:
            typer.echo("Aborting...")
            executor.shutdown(cancel_futures=True)
            raise typer.Exit(1)


@cli.command()
def search(
    text: str,
    user: str = None,
    lang: str = None,
    format: str = "",
    download_parts: bool = False,
    spacing_secs: int = 5,
    merge: bool = False,
):
    results = []
    for row in db.search(text, uploader_id=user, lang=lang):
        results.append(row)

    if not results:
        print("No results")
        return

    if download_parts:
        print(f"Downloading {len(results)} parts...")
        for row in results:
            utils.download_part(
                row,
                folder=f"output-{text.replace(' ', '-').lower()}",
                spacing_secs=spacing_secs,
            )
        return

    if format == "json":
        print(json.dumps(results, indent=2))
    else:
        print(tabulate(results, headers="keys", maxcolwidths=20))


@cli.command()
def list_channels(format: str = ""):
    channels = list(db.get_channels())

    if format == "json":
        print(json.dumps(channels, indent=2))
    else:
        print(tabulate(channels, headers="keys", tablefmt="simple"))
        

@cli.command()
def stats(format: str = ""):
    _db = db.get_db()

    stats = {
        "channels": _db["channels"].count,
        "videos": _db["videos"].count,
    }

    if format == "json":
        print(json.dumps(stats, indent=2))
    else:
        print(tabulate(stats.items()))


@cli.command()
def remove_channel(uploader_id: str = typer.Argument(help="Channel user handle (ie. @user)")):
    db.delete_channel(uploader_id)


@cli.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Run datasette, supports all datasette options",
)
def server(ctx: typer.Context):
    try:
        import datasette
    except ImportError:
        typer.echo("datasette not installed. Run `pip install yt-supercut[datasette]`")
        raise typer.Exit(1)

    args = [
        "datasette",
        db.DB_PATH,
        "--metadata",
        str(Path().parent / "metadata.json"),
        *ctx.args
    ] 

    subprocess.run(args)


if __name__ == "__main__":
    cli()