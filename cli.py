import socket
import subprocess
import time
from threading import Thread

import click
import requests

from utils import resource_path

API_BASE_URL = "https://spoofy.baka.tokyo/"
SPOTIFY_CONNECT_NAME = "Spoofy Bot"
SAMPLE_RATE = 44100
CHANNELS = 2
BITS = 16
SAMPLE_SIZE = (SAMPLE_RATE * BITS * CHANNELS) // 8
CHUNK_SIZE = SAMPLE_SIZE // 4


@click.command()
@click.argument('link_code')
@click.option('--username', "-u", help="Your Spotify username or email address")
@click.option('--password', '-p', help="The password for your Spotify account")
@click.option('--bitrate', "-b", default=320, help="The bitrate of the stream")
def spoofy(username: str, password: str, bitrate: int, link_code: str):
    """
    Connect your Spotify account to the Spoofy bot through the CLI
    """
    librespot_path = resource_path("libraries/librespot")
    args = [
        librespot_path,
        "--name", SPOTIFY_CONNECT_NAME,
        "--username", username,
        "--password", password,
        "--bitrate", str(bitrate),
        "--disable-discovery",
        "--device-type", "speaker",
        "--backend", "pipe",
        "--initial-volume", "100",
        "--enable-volume-normalisation"
        ]
    process = subprocess.Popen(args=args, stdout=subprocess.PIPE)
    res = requests.get(API_BASE_URL + "connect/", params={"user": username, "link_code": link_code})
    data = res.json()
    address, port = data['address'], data['port']
    output_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stdout_thread = Thread(target=output_worker, args=[address, port, output_socket, process.stdout])
    stdout_thread.start()
    res = requests.get(API_BASE_URL + "start/", params={"link_code": link_code})


def output_worker(address: str, port: int, sock: socket.socket, stdout):
    # Connect to address
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.connect((address, port))

    # Start sending data
    try:
        while (not stdout.closed):
            # Read and send 0.25 seconds of audio
            sock.send(stdout.read(CHUNK_SIZE))
            # Wait 250ms before reading the next chunk
            time.sleep(0.25)
    except (ConnectionResetError, BrokenPipeError, OSError):
        print("Disconnected from bot. Either user disconnected, bot disconnected or there are connection problems.")
    finally:
        if sock is not None:
            sock.close()

    print("OutputWorker stopped")


if __name__ == "__main__":
    spoofy(standalone_mode=False)
