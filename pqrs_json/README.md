# Agente Resumidor PQRSD

Este modulo agrega el agente resumidor en la misma estructura de `pqrs_json`.

## Entrada esperada

Archivo JSON con arreglo de objetos clasificados o un objeto unico, por ejemplo:

- `radicado`
- `pqrs`
- `canal`
- `fecha_utc`
- `username`
- `nombre`
- `secretaria`
- `clasificacion`
- `fecha_limite`

Tambien soporta payload del bot con `usuario.username` y `usuario.nombre`.

## Salida

Genera un archivo JSON conservando los campos de entrada y agregando solo:

- `titulo_ia`
- `resumen_ia`

## Ejecucion

```bash
python pqrs_json/pqrs_resumidor_agent.py --input pqrs_json/pqrs_ruteadas_20260419T130050Z.json
```

Con salida personalizada:

```bash
python pqrs_json/pqrs_resumidor_agent.py --input pqrs_json/pqrs_ruteadas_20260419T130050Z.json --output pqrs_json/pqrs_resumidas_20260419T133000Z.json
```
