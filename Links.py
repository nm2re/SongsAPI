from pathlib import Path

class Link:
    ITUNES_URL = "https://itunes.apple.com/search"
    DOWNLOAD_DIR = Path("C:/Users/Administrator/Downloads/Albums")
    COOKIES_URL = Path("C:/Users/Administrator/cookies.txt")

    @classmethod
    def setup(cls):
        cls.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)  # creates a parent for you incase you dont have one (directory)