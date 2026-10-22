---
name: publicar
description: Publicar una versión nueva de BlackPDF en GitHub (número de versión, notas, etiqueta, subida, CI e instaladores). Solo la lanza el usuario con /publicar.
disable-model-invocation: true
argument-hint: "[versión, por ejemplo 1.1.0]"
---

# Publicar BlackPDF $ARGUMENTS

Es lo único que sale del ordenador del usuario. Cada paso hacia fuera, solo con su OK.

1. **Estado**: rama `main` y nada sin commit salvo `ejemplo.pdf`. Novedades desde la última versión:
   `git log $(git describe --tags --abbrev=0)..HEAD --oneline`.
2. **Versión**: si no viene en los argumentos, propón una (menor si hay funciones nuevas, parche si
   solo hay arreglos). Cámbiala en `pyproject.toml` (`version`) y en `blackpdf/__init__.py` (`__version__`).
3. **Notas** en `RELEASE_NOTES.md`: una sección «What's new in X.Y.Z» encima de «Highlights» y su
   versión en español en la parte de abajo. Cuenta lo que cambia para quien usa el programa, sin
   jerga técnica, sin emojis y para todo el mundo (no solo ingenieros). GitHub añade solo la lista de commits.
4. **Verificar**: `scripts/verificar.py` completo en VERDE.
5. **OK del usuario**: enséñale la versión y las notas, y pregúntale si publicar. Si no hay un sí claro, para.
6. **Subir**: commit «Versión X.Y.Z», etiqueta anotada `git tag -a vX.Y.Z -m "BlackPDF X.Y.Z"`,
   luego `git push origin main` y `git push origin vX.Y.Z` (los permisos lo vuelven a confirmar).
7. **CI**: la etiqueta lanza `.github/workflows/build.yml` (pruebas, Windows, Linux y release, unos
   20-30 min). Síguelo en https://github.com/Black-Robots/BlackPDF/actions (página HTML; la API sin
   autenticar solo admite 60 peticiones por hora, no la consultes en bucle). No hay `gh` instalado.
8. **Release**: en https://github.com/Black-Robots/BlackPDF/releases/tag/vX.Y.Z tienen que estar
   `BlackPDF-X.Y.Z-Setup.exe`, `BlackPDF-X.Y.Z-x86_64.AppImage`, `blackpdf_X.Y.Z_amd64.deb` y
   `SHA256SUMS.txt`. No cambies esos nombres: el actualizador de la app los busca así.
9. **Web**: `build/web` (repo Black-Robots/theblackrobotsfoundation.github.io; un push a `main` la
   despliega). Enlaza a `releases/latest`, así que normalmente no hay que tocarla. Si conviene
   contar alguna novedad, propón el cambio aparte y espera otro OK. Reglas de la web: para todo el
   mundo, sin temática de robótica, sin capturas nuevas sin aprobar y sin preguntas frecuentes.
10. **Si CI falla**: lee el fallo (en Windows, el final del registro sale como anotación del paso),
    arréglalo en un commit nuevo y pregunta antes de mover o volver a crear la etiqueta.

Commits y etiquetas van como `DiegoRayado <hello@blackrobots.org>`; nunca con el correo personal.
