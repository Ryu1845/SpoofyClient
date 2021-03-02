from gui_controller import SpoofyClientApp

CLIENT_VERSION = "v1.0"
API_BASE_URL = "https://spoofy.baka.tokyo/"

GITHUB_LINK_BOT = "https://github.com/Kanakonn/Spoofy"
GITHUB_LINK_CLIENT = "https://github.com/Kanakonn/SpoofyClient"
GITHUB_LATEST_RELEASE_API_URL = "https://api.github.com/repos/Kanakonn/SpoofyClient/releases/latest"

if __name__ == "__main__":
    spoofy_client = SpoofyClientApp(0)
    spoofy_client.MainLoop()
