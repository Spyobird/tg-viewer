def main() -> None:
    from .app import app

    # Bound to localhost only: the imported chats are private.
    app.run(host="127.0.0.1", port=8000)
