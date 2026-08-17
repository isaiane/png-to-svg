# PNG to SVG Studio

Conversor local de imagens raster para SVG com paths editáveis, controle de cores e preview em navegador.

## Recursos

- Vetorização colorida com 2 a 64 cores
- Modo preto e branco com threshold e inversão
- Remoção automática ou explícita de fundo
- Controles de suavização, simplificação e remoção de ruído
- Preview lado a lado em uma interface localhost
- Download de SVG path-based, sem imagem raster incorporada
- Paleta personalizada em hexadecimal

## Instalação

```bash
git clone https://github.com/isaiane/png-to-svg.git
cd png-to-svg
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

No Windows, use `.venv\Scripts\python.exe` no lugar de `.venv/bin/python`.

## Painel web

```bash
.venv/bin/python scripts/web_app.py --open
```

Abra [http://127.0.0.1:8765](http://127.0.0.1:8765). Use `--port 9000` para escolher outra porta.

## Linha de comando

```bash
.venv/bin/python scripts/png_to_svg.py input.png output.svg \
  --colors 8 --background auto --simplify 1 --min-area 4
```

Exemplo preto e branco:

```bash
.venv/bin/python scripts/png_to_svg.py logo.png logo.svg \
  --mode bw --threshold 150 --simplify 0.6
```

Exemplo com paleta fixa:

```bash
.venv/bin/python scripts/png_to_svg.py input.png output.svg \
  --palette '#102A43,#2CB1BC,#F0B429,#FFFFFF'
```

Execute `python3 scripts/png_to_svg.py --help` para consultar todos os parâmetros.

## Estrutura

```text
assets/web/index.html     Interface local
scripts/web_app.py        Servidor localhost e API
scripts/png_to_svg.py     Motor de vetorização
requirements.txt          Dependências Python
```

As imagens enviadas ao painel são processadas em diretórios temporários e descartadas após cada requisição.
