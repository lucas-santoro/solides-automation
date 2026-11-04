"""Garantias do navegador da automação: sem localização, sem câmera e nada guardado entre operações."""

from fake_tangerino import FakeTangerino

from solides_mcp.browser import BrowserManager

# 127.0.0.1 é contexto seguro, então geolocation e mediaDevices existem na página como no site real.
_PEDIR_PERMISSOES = r"""async () => {
  const r = {};
  for (const nome of ['geolocation', 'camera', 'microphone']) {
    r[nome] = (await navigator.permissions.query({name: nome})).state;
  }
  r.posicao = await new Promise(res => {
    const t = setTimeout(() => res('sem resposta'), 3000);
    navigator.geolocation.getCurrentPosition(
      () => { clearTimeout(t); res('PERMITIDO'); },
      e => { clearTimeout(t); res('negado:' + e.code); },
    );
  });
  r.video = await Promise.race([
    navigator.mediaDevices.getUserMedia({video: true})
      .then(s => { s.getTracks().forEach(t => t.stop()); return 'PERMITIDO'; }, e => 'negado:' + e.name),
    new Promise(res => setTimeout(() => res('sem resposta'), 3000)),
  ]);
  return r;
}"""


async def test_localizacao_e_camera_negadas(browser: BrowserManager, fake: FakeTangerino) -> None:
    async with browser.page() as page:
        await page.goto(fake.base_url)
        resultado = await page.evaluate(_PEDIR_PERMISSOES)

    assert (resultado["geolocation"], resultado["camera"], resultado["microphone"]) == ("denied",) * 3
    assert resultado["posicao"] == "negado:1"  # PERMISSION_DENIED, sem ficar esperando um prompt
    assert resultado["video"].startswith("negado:")


async def test_nada_passa_de_uma_operacao_para_outra(browser: BrowserManager, fake: FakeTangerino) -> None:
    async with browser.page() as page:
        await page.goto(fake.base_url)
        # O register.js guarda o dispositivo assim (localStorage + cookie _tngDeviceId).
        await page.evaluate(
            "() => { document.cookie = '_tngDeviceId=abc; max-age=63072000; path=/';"
            " localStorage.setItem('codigoDispositivo', 'abc'); }"
        )
        assert await page.context.cookies() != []

    async with browser.page() as page:
        assert await page.context.cookies() == []
        await page.goto(fake.base_url)
        guardado = await page.evaluate("() => [document.cookie, localStorage.getItem('codigoDispositivo')]")

    assert guardado == ["", None]
