import os
import re
import sys


def resource_path(relative):
    return os.path.join(
        getattr(sys, "_MEIPASS", os.path.abspath(".")),
        relative
    )

def strip_html(message):
    if "<body" in message and "</body>" in message:
        body = message.split("<body", maxsplit=1)[1]
        body = body.split(">", maxsplit=1)[1]
        body = body.split("</body>", maxsplit=1)[0]
    else:
        body = message
    return re.sub(re.compile(r"<.*?>"), "", body)
