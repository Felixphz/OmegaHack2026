# Agente Resumidor PQRSD

Este modulo agrega el agente resumidor en la misma estructura de `pqrs_json`.

## Entrada esperada

Archivo JSON con arreglo de objetos clasificados, por ejemplo:

- `radicado`
- `pqrs`
- `canal`
- `fecha_utc`
- `username`
- `nombre`
- `secretaria`
- `division` (opcional)
- `tipo` (opcional)

## Salida

Genera un archivo JSON con los campos originales y adicionales:

- `dependencia_sugerida`
- `titulo_ia`
- `resumen_bullets`
- `borrador_respuesta`
- `resumido_en_utc`

## Ejecucion

```bash
python pqrs_json/pqrs_resumidor_agent.py --input pqrs_json/pqrs_ruteadas_20260419T130050Z.json
```

Con salida personalizada:

```bash
python pqrs_json/pqrs_resumidor_agent.py --input pqrs_json/pqrs_ruteadas_20260419T130050Z.json --output pqrs_json/pqrs_resumidas_20260419T133000Z.json
```
