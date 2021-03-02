import os
import platform
import socket
import subprocess
import time
from threading import Thread
from typing import Optional, IO, AnyStr, List

import requests

from utils import resource_path, strip_html

SPOTIFY_CONNECT_NAME = "Spoofy Bot"
SAMPLE_RATE = 44100
CHANNELS = 2
BITS = 16
SAMPLE_SIZE = (SAMPLE_RATE * BITS * CHANNELS) // 8
CHUNK_SIZE = SAMPLE_SIZE // 4


class LogTarget:
    def process(self, message):
        pass

class StandardOutTarget(LogTarget):
    def __init__(self, name: str):
        self.name = name

    def process(self, message):
        print(f"[{self.name}] {message}")


def log_worker(controller: 'SpotifyController', targets: List, stdout: Optional[IO[AnyStr]]):
    while not controller.stop_threads:
        output = stdout.readline().decode("utf-8").strip()
        if output:
            for target in targets:
                if target is not None:
                    target.process(output)
    print(f"LogWorker stopped")


def output_worker(controller: 'SpotifyController', address: str, port: int, sock: socket.socket, stdout: Optional[IO[AnyStr]]):
    # Connect to address
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.connect((address, port))

    # Clear stdout
    if stdout.seekable() and platform.system() == "Linux":
        stdout.seek(-8, os.SEEK_END)
    else:
        print("Cannot seek in stdout")

    # Start sending data
    try:
        while (not stdout.closed) or (not controller.stop_threads):
            # Read and send 0.25 seconds of audio
            sock.send(stdout.read(CHUNK_SIZE))
            # Wait 250ms before reading the next chunk
            time.sleep(0.25)
    except (ConnectionResetError, BrokenPipeError, OSError):
        print("Disconnected from bot. Either user disconnected, bot disconnected or there are connection problems.")
        controller.on_bot_disconnect()
    finally:
        if sock is not None:
            sock.close()

    print(f"OutputWorker stopped")


class SpotifyController:
    _instance: Optional['SpotifyController'] = None

    def __init__(self, client, process):
        self.client = client
        self.process: subprocess.Popen = process
        self.output_threads: List = []
        self.log_threads: List = []
        self.log_targets: List = []
        self.stop_threads: bool = False
        self.address: Optional[str] = None
        self.port: Optional[int] = None
        self.output_socket: Optional[socket.socket] = None

    @classmethod
    def get_instance(cls):
        if cls._instance is not None:
            return cls._instance
        return None

    @classmethod
    def remove_inst(cls):
        cls._instance = None

    @classmethod
    def stop_for_user(cls):
        inst = cls.get_instance()
        if inst is not None:
            inst.stop()


    @classmethod
    def create(cls, client, spotify_username, spotify_password, bitrate=160):

        # Check if no existing instance exists
        inst = cls.get_instance()
        if inst is not None:
            raise ValueError("Instance already exists!")

        # Get proper path to librespot
        if platform.system() == "Linux":
            librespot_path = resource_path("libraries/librespot")
        elif platform.system() == "Windows":
            librespot_path = resource_path("libraries/librespot.exe")
        else:
            raise ValueError(f"Unsupported platform: '{platform.system()}'")

        # Create a FIFO pipe for librespot to use
        args = [
            librespot_path,
            "--name", SPOTIFY_CONNECT_NAME,
            "--username", spotify_username,
            "--password", spotify_password,
            "--bitrate", str(bitrate),
            "--disable-discovery",
            "--device-type", "speaker",
            "--backend", "pipe",
            "--initial-volume", "100",
            "--enable-volume-normalisation"
        ]
        print(f"Creating player...")

        # Create librespot instance
        if platform.system() == "Windows":
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        else:
            startup_info = None
        process = subprocess.Popen(
            args=args,
            startupinfo=startup_info,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE
        )

        inst = SpotifyController(client=client, process=process)
        inst.setup_log_thread()
        cls._instance = inst
        return inst

    def setup_log_thread(self):
        tid = len(self.log_threads) + 1
        self.log_targets.append(StandardOutTarget(f"Player-{tid}"))
        stderr_thread = Thread(target=log_worker, args=[self, self.log_targets, self.process.stderr])
        stderr_thread.start()
        self.log_threads.append(stderr_thread)

    def setup_output_thread(self):
        if self.address is None or self.port is None:
            raise ValueError("Address or port not set.")
        self.output_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stdout_thread = Thread(target=output_worker, args=[self, self.address, self.port,
                                                           self.output_socket, self.process.stdout])
        stdout_thread.start()
        self.output_threads.append(stdout_thread)

    def on_bot_disconnect(self):
        self.client.on_bot_disconnect()

    def disconnect(self):
        # Close the output socket if it is open
        if self.output_socket is not None:
            self.output_socket.close()
            self.output_socket = None

        # Join output threads
        for thread in self.output_threads:
            # Join the thread, wait for 1 second for it to quit.
            thread.join(timeout=1)
            # If thread is still alive after timeout, kill it.
            if thread.is_alive():
                print(f"Thread {thread} failed to stop")

        self.output_threads = []

    def stop(self):
        # Stop Spotify subprocess
        self.process.terminate()

        # If not terminated immediately, wait 1 second.
        if self.process.poll() is None:
            time.sleep(1)

        # If still not terminated after 1 second, kill it.
        if self.process.poll() is None:
            self.process.kill()

        # Signal to stop log threads
        self.stop_threads = True

        # Close the output socket if it is open
        if self.output_socket is not None:
            self.output_socket.close()
            self.output_socket = None

        # Join log threads
        for thread in self.log_threads:
            # Join the thread, wait for 1 second for it to quit.
            thread.join(timeout=1)
            # If thread is still alive after timeout, kill it.
            if thread.is_alive():
                print(f"Thread {thread} failed to stop")

        # Join output threads
        for thread in self.output_threads:
            # Join the thread, wait for 1 second for it to quit.
            thread.join(timeout=1)
            # If thread is still alive after timeout, kill it.
            if thread.is_alive():
                print(f"Thread {thread} failed to stop")

        # Remove self from instance list
        SpotifyController.remove_inst()

    def wait(self):
        self.process.wait()

    def check_req(self, username):
        from main import API_BASE_URL
        # Connects to the API and checks if everything is good to go
        try:
            res = requests.get(API_BASE_URL + "check/", params={"user": username})
        except ConnectionError as e:
            return False, f"Connection error: {e}", "Connection error."

        if res.status_code == 200:
            data = res.json()
            return data['linked'], "", ""

        content = strip_html(res.content.decode("utf-8"))
        return False, f"HTTP error {res.status_code} - {content}", "Connection error."

    def connect_req(self, username, link_code):
        from main import API_BASE_URL
        try:
            res = requests.get(API_BASE_URL + "connect/", params={"user": username, "link_code": link_code})
        except ConnectionError as e:
            return False, f"Connection error: {e}", "Connection error."
        if res.status_code == 200:
            data = res.json()
            if data.get("error", False):
                return False, data['msg'], data['short_msg']
            else:
                return True, data['address'], data['port']

        content = strip_html(res.content.decode("utf-8"))
        return False, f"HTTP error {res.status_code} - {content}", "Connection error."

    def start_req(self, link_code):
        from main import API_BASE_URL
        try:
            res = requests.get(API_BASE_URL + "start/", params={"link_code": link_code})
        except ConnectionError as e:
            return False, f"Connection error: {e}", "Connection error."
        if res.status_code == 200:
            data = res.json()
            if data.get("error", False):
                return False, data['msg'], data['short_msg']
            else:
                return True, "", ""

        content = strip_html(res.content.decode("utf-8"))
        return False, f"HTTP error {res.status_code} - {content}", "Connection error."
