from pathlib import Path


class SolidesError(Exception):
    """Falha esperada na interação com o Sólides; a mensagem vai para o cliente MCP."""

    screenshot: Path | None = None


class NavegadorError(SolidesError):
    """Erro do Playwright (rede, timeout, elemento que não apareceu...)."""


class LoginError(SolidesError):
    """O login não foi concluído (credencial inválida, mensagem do site, volta para a tela de login...)."""


class CaptchaRequired(LoginError):
    """O site passou a exigir reCAPTCHA; a automação não tenta resolvê-lo."""


class LancamentoError(SolidesError):
    """O site recusou um lançamento de ponto em atraso; `lancados` tem o que foi salvo antes do erro."""

    def __init__(self, mensagem: str, lancados: list[str]) -> None:
        super().__init__(mensagem)
        self.lancados = lancados
