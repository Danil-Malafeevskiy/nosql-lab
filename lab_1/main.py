import uvicorn

from lab_1.app.app import create_app
from lab_1.app.settings import get_settings


app = create_app()


def run() -> None:
    s = get_settings()
    uvicorn.run(app, host=s.app_host, port=s.app_port)


if __name__ == "__main__":
    run()
