class Notifier:
    def send(self, posting, score: float, reason: str) -> None:
        raise NotImplementedError
