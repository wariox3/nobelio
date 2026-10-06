# Festivos de Colombia

`Festivo.gc` es una lista **propia de nobelio**, no de la DIAN: los festivos
nacionales, para contar días hábiles (`apps/catalogos/calendario.py`). Con ellos
se calculan los plazos de los eventos RADIAN.

- **Código**: la fecha, `AAAA-MM-DD`. **Id**: la fecha como número
  (`2026-01-12` → `20260112`). **Nombre**: el del festivo.
- Trae **2026 y 2027**.

## Mantenimiento: cada año

Hay que agregar el año siguiente **antes del 1 de enero** y cargarlo con
`python manage.py cargar_catalogos`. Si falta un año que un plazo necesita,
`calendario.py` no adivina: lanza `FestivosNoCargados`.

Son 18 por año (Ley 51 de 1983, «ley Emiliani»):

| Regla | Festivos |
|-------|----------|
| Fecha fija | 1 de enero, 1 de mayo, 20 de julio, 7 de agosto, 8 de diciembre, 25 de diciembre |
| Se trasladan al lunes siguiente si no caen en lunes | Reyes (6 ene), San José (19 mar), San Pedro y San Pablo (29 jun), Asunción (15 ago), Día de la Raza (12 oct), Todos los Santos (1 nov), Independencia de Cartagena (11 nov) |
| Según la Pascua, sin trasladar | Jueves Santo (Pascua − 3), Viernes Santo (Pascua − 2) |
| Según la Pascua, al lunes | Ascensión (Pascua + 43), Corpus Christi (Pascua + 64), Sagrado Corazón (Pascua + 71) |

Antes de cargar un año, cotejarlo con el calendario oficial publicado: si el
Congreso agrega o mueve un festivo, la tabla de arriba deja de bastar.
