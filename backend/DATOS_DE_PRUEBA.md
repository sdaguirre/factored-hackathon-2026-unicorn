# Datos de prueba del asistente

Todo es **sintético**; no hay personas reales. Se genera con `python scripts/gen_test_data.py` a partir de los datos que use el backend (`data/fixture/` por defecto).

## Cómo probar

1. Abra la interfaz (`http://localhost:8080`) y escriba el **documento** de un cliente de abajo.
2. Responda las **preguntas de seguridad** con la ficha del cliente. Las preguntas cambian en cada intento y salen de estos mismos datos: su ocupación registrada, la ciudad donde abrió un producto (solo si lo abrió en sucursal), el año de apertura de un producto y el año en que se hizo cliente. Un producto se nombra por su tipo (o «el más antiguo» si hay varios del mismo tipo). Las opciones incorrectas son inventadas y las ciudades son siempre del mismo país. Nunca se pregunta por montos ni fechas exactas.
3. Pruebe las frases sugeridas de cada escenario. Para portugués, cambie el selector de idioma antes de empezar.

**Política de crédito 0.4** (`docs/CREDIT_RULES.md`), la misma que calcula gold: bandas A–E, tasa de la grilla por plazo o nivel de tarjeta, plazo máximo por banda y por edad (el crédito termina antes de los 75 años), límite del 20% sin margen. Sin monto, el asistente propone primero lo más alto. Tras un resultado pregunta a todos por igual si alguien más del hogar aporta ingresos. Los montos se calculan en USD y se muestran en la moneda local del cliente.

**Flujos de crédito:** tras una evaluación favorable el asistente pregunta si quiere avanzar. Si dice que sí, pide solo los documentos que al cliente le **faltan** (los que el banco ya tiene figuran en cada ficha); el chat no recibe archivos: el cliente confirma que cuenta con ellos. Con todo en orden deriva a un asesor. Al terminar (despedida o botón «Terminar conversación») entrega el **resumen** de la propuesta y avisa que el detalle llegará por correo en un PDF: el correo es **simulado** (no se envía) y el PDF se descarga desde la interfaz. Si el cliente habla en otra moneda («dólares», «pesos colombianos»), el asistente la convierte a la de su ingreso con la tasa de referencia del conjunto de datos (fecha de corte, no cotización en vivo).

Tres intentos fallidos bloquean ese documento 15 minutos (también un documento inexistente, a propósito). Para desbloquear, reinicie el backend: `docker compose restart chat-backend`.

Los montos de los ejemplos están en la moneda local de cada cliente. Los resultados indicados (eligible, declined…) son los de la política 0.4 con los datos de hoy.

## Escenario A: Oferta proactiva (gold: offer_mode = proactive): la recibe al cerrar, empezando por lo más alto

**Bruno** · documento `53464097` · México · moneda MXN · ingreso 95.000 MXN · score 740 (banda A) · mora máx. 0 días · marketing: sí · gold: `proactive`, motivos ninguno

- Ocupación registrada: **Ingeniero/a**
- Año en que se hizo cliente: **2019**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - cuenta de ahorro: apertura en sucursal de **Tijuana**, año **2022**
  - tarjeta de crédito: apertura por Web (no se pregunta la ciudad), año **2024**
  - tarjeta de débito: apertura por Web (no se pregunta la ciudad), año **2019**
- Documentos que el banco ya tiene: comprobante de domicilio, copia del documento de identidad, comprobante de ingresos (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿qué ofertas tengo?» → lo más alto de cada producto (préstamo, tarjeta por nivel, hipoteca)
- «gracias, eso es todo» (debe llegar la oferta)
- «quiero un préstamo» → propone lo más alto y pregunta el monto; «sí» toma ese máximo
- «quiero un préstamo de 293237 a 60 meses» → eligible; luego pregunta si alguien más del hogar aporta ingresos
- «necesito un préstamo de 762417 a 60 meses» → declined (pasa el 20% del ingreso)
- «sí» / «mi pareja gana 20.000» → pide el ingreso y las cuotas de esa persona y recalcula (oferta condicional, F03)
- «necesito un préstamo de 8000 dólares a 60 meses» → convierte a MXN con la tasa de referencia
- «quiero una tarjeta de crédito» → el nivel más alto disponible (Clásica, Gold, Platinum o Black)
- «sí» (a «¿Le gustaría que avancemos?») → registra la oferta aceptada y pide solo los documentos que falten
- «gracias, eso es todo» → resumen de la propuesta y aviso del PDF por correo (simulado)
- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

**Alicia** · documento `15455881` · Colombia · moneda COP · ingreso 7.200.000 COP · score 700 (banda B) · mora máx. 0 días · marketing: sí · gold: `proactive`, motivos ninguno

- Ocupación registrada: **Profesional independiente**
- Año en que se hizo cliente: **2023**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - tarjeta de crédito: apertura en sucursal de **Barranquilla**, año **2021**
  - cuenta de ahorro: apertura en sucursal de **Barranquilla**, año **2020**
  - tarjeta de débito: apertura en sucursal de **Cartagena**, año **2019**
- Documentos que el banco ya tiene: copia del documento de identidad (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿qué ofertas tengo?» → lo más alto de cada producto (préstamo, tarjeta por nivel, hipoteca)
- «gracias, eso es todo» (debe llegar la oferta)
- «quiero un préstamo» → propone lo más alto y pregunta el monto; «sí» toma ese máximo
- «quiero un préstamo de 20000000 a 60 meses» → eligible; luego pregunta si alguien más del hogar aporta ingresos
- «necesito un préstamo de 52000000 a 60 meses» → declined (pasa el 20% del ingreso)
- «sí» / «mi pareja gana 20.000» → pide el ingreso y las cuotas de esa persona y recalcula (oferta condicional, F03)
- «necesito un préstamo de 8000 dólares a 60 meses» → convierte a COP con la tasa de referencia
- «quiero una tarjeta de crédito» → el nivel más alto disponible (Clásica, Gold, Platinum o Black)
- «sí» (a «¿Le gustaría que avancemos?») → registra la oferta aceptada y pide solo los documentos que falten
- «gracias, eso es todo» → resumen de la propuesta y aviso del PDF por correo (simulado)
- «quiero una hipoteca a 30 años» → explica que terminaría después de los 75 años y ofrece los plazos posibles
- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

**Valentín** · documento `11542972` · Argentina · moneda ARS · ingreso 585.999 ARS · score 565 (banda D) · mora máx. 0 días · marketing: sí · gold: `proactive`, motivos ninguno

- Ocupación registrada: **Director/a**
- Año en que se hizo cliente: **2024**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - tarjeta de débito: apertura en sucursal de **Córdoba**, año **2021**
  - tarjeta de crédito: apertura en sucursal de **Mendoza**, año **2020**
  - cuenta corriente: apertura por Web (no se pregunta la ciudad), año **2018**
  - cuenta de ahorro: apertura por App (no se pregunta la ciudad), año **2018**
- Documentos que el banco ya tiene: copia del documento de identidad, comprobante de ingresos (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿qué ofertas tengo?» → lo más alto de cada producto (préstamo, tarjeta por nivel, hipoteca)
- «gracias, eso es todo» (debe llegar la oferta)
- «quiero un préstamo» → propone lo más alto y pregunta el monto; «sí» toma ese máximo
- «quiero un préstamo de 1330066 a 36 meses» → eligible; luego pregunta si alguien más del hogar aporta ingresos
- «necesito un préstamo de 3458172 a 36 meses» → declined (pasa el 20% del ingreso)
- «sí» / «mi pareja gana 20.000» → pide el ingreso y las cuotas de esa persona y recalcula (oferta condicional, F03)
- «necesito un préstamo de 8000 dólares a 36 meses» → convierte a ARS con la tasa de referencia
- «quiero una tarjeta de crédito» → el nivel más alto disponible (Clásica, Gold, Platinum o Black)
- «sí» (a «¿Le gustaría que avancemos?») → registra la oferta aceptada y pide solo los documentos que falten
- «gracias, eso es todo» → resumen de la propuesta y aviso del PDF por correo (simulado)
- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario B: Elegible sin consentimiento de marketing (on_customer_interest): puede pedir crédito, nunca recibe oferta proactiva

**Valentín** · documento `31667923` · México · moneda MXN · ingreso 80.000 MXN · score 756 (banda A) · mora máx. 0 días · marketing: no · gold: `on_customer_interest`, motivos ninguno

- Ocupación registrada: **Gerente**
- Año en que se hizo cliente: **2023**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - cuenta corriente: apertura en sucursal de **Ciudad de México**, año **2019**
  - tarjeta de débito: apertura por Web (no se pregunta la ciudad), año **2025**
  - tarjeta de crédito: apertura en sucursal de **Querétaro**, año **2024**
- Documentos que el banco ya tiene: comprobante de domicilio, copia del documento de identidad (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿qué ofertas tengo?» → lo más alto de cada producto (préstamo, tarjeta por nivel, hipoteca)
- «gracias, eso es todo» (NO debe llegar oferta)
- «quiero un préstamo» → propone lo más alto y pregunta el monto; «sí» toma ese máximo
- «quiero un préstamo de 256042 a 60 meses» → eligible; luego pregunta si alguien más del hogar aporta ingresos
- «necesito un préstamo de 665709 a 60 meses» → declined (pasa el 20% del ingreso)
- «sí» / «mi pareja gana 20.000» → pide el ingreso y las cuotas de esa persona y recalcula (oferta condicional, F03)
- «necesito un préstamo de 8000 dólares a 60 meses» → convierte a MXN con la tasa de referencia
- «quiero una tarjeta de crédito» → el nivel más alto disponible (Clásica, Gold, Platinum o Black)
- «sí» (a «¿Le gustaría que avancemos?») → registra la oferta aceptada y pide solo los documentos que falten
- «gracias, eso es todo» → resumen de la propuesta y aviso del PDF por correo (simulado)
- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

**Bruno** · documento `41532215` · Colombia · moneda COP · ingreso 5.800.000 COP · score 640 (banda C) · mora máx. 0 días · marketing: no · gold: `on_customer_interest`, motivos ninguno

- Ocupación registrada: **Gerente**
- Año en que se hizo cliente: **2024**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - cuenta de ahorro: apertura en sucursal de **Medellín**, año **2024**
  - cuenta corriente: apertura por App (no se pregunta la ciudad), año **2021**
  - tarjeta de débito: apertura en sucursal de **Medellín**, año **2022**
  - tarjeta de crédito: apertura por App (no se pregunta la ciudad), año **2021**
- Documentos que el banco ya tiene: copia del documento de identidad, comprobante de ingresos (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿qué ofertas tengo?» → lo más alto de cada producto (préstamo, tarjeta por nivel, hipoteca)
- «gracias, eso es todo» (NO debe llegar oferta)
- «quiero un préstamo» → propone lo más alto y pregunta el monto; «sí» toma ese máximo
- «quiero un préstamo de 13800000 a 48 meses» → eligible; luego pregunta si alguien más del hogar aporta ingresos
- «necesito un préstamo de 35880000 a 48 meses» → declined (pasa el 20% del ingreso)
- «sí» / «mi pareja gana 20.000» → pide el ingreso y las cuotas de esa persona y recalcula (oferta condicional, F03)
- «necesito un préstamo de 8000 dólares a 48 meses» → convierte a COP con la tasa de referencia
- «quiero una tarjeta de crédito» → el nivel más alto disponible (Clásica, Gold, Platinum o Black)
- «sí» (a «¿Le gustaría que avancemos?») → registra la oferta aceptada y pide solo los documentos que falten
- «gracias, eso es todo» → resumen de la propuesta y aviso del PDF por correo (simulado)
- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario C: Plazo limitado por la edad al vencimiento (75 años, política 0.4): la hipoteca no llega al máximo de su banda

**Alicia** · documento `15455881` · Colombia · moneda COP · ingreso 7.200.000 COP · score 700 (banda B) · mora máx. 0 días · marketing: sí · gold: `proactive`, motivos ninguno

- Ocupación registrada: **Profesional independiente**
- Año en que se hizo cliente: **2023**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - tarjeta de crédito: apertura en sucursal de **Barranquilla**, año **2021**
  - cuenta de ahorro: apertura en sucursal de **Barranquilla**, año **2020**
  - tarjeta de débito: apertura en sucursal de **Cartagena**, año **2019**
- Documentos que el banco ya tiene: copia del documento de identidad (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿qué ofertas tengo?» → lo más alto de cada producto (préstamo, tarjeta por nivel, hipoteca)
- «gracias, eso es todo» (debe llegar la oferta)
- «quiero un préstamo» → propone lo más alto y pregunta el monto; «sí» toma ese máximo
- «quiero un préstamo de 20000000 a 60 meses» → eligible; luego pregunta si alguien más del hogar aporta ingresos
- «necesito un préstamo de 52000000 a 60 meses» → declined (pasa el 20% del ingreso)
- «sí» / «mi pareja gana 20.000» → pide el ingreso y las cuotas de esa persona y recalcula (oferta condicional, F03)
- «necesito un préstamo de 8000 dólares a 60 meses» → convierte a COP con la tasa de referencia
- «quiero una tarjeta de crédito» → el nivel más alto disponible (Clásica, Gold, Platinum o Black)
- «sí» (a «¿Le gustaría que avancemos?») → registra la oferta aceptada y pide solo los documentos que falten
- «gracias, eso es todo» → resumen de la propuesta y aviso del PDF por correo (simulado)
- «quiero una hipoteca a 30 años» → explica que terminaría después de los 75 años y ofrece los plazos posibles
- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario D: Sin ingreso registrado (R05): el asistente pide que lo declare y la oferta queda condicional (F03)

**Alicia** · documento `52410090` · México · moneda MXN · ingreso sin ingreso registrado · score 790 (banda A) · mora máx. 0 días · marketing: no · gold: `none`, motivos R05_INCOME_MISSING

- Ocupación registrada: **Médico/a**
- Año en que se hizo cliente: **2020**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - cuenta corriente: apertura en sucursal de **Monterrey**, año **2025**
  - tarjeta de crédito: apertura por App (no se pregunta la ciudad), año **2021**
  - cuenta de ahorro: apertura en sucursal de **Tijuana**, año **2019**
  - tarjeta de débito: apertura por App (no se pregunta la ciudad), año **2020**
- Documentos que el banco ya tiene: comprobante de domicilio, copia del documento de identidad, comprobante de ingresos (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «quiero un préstamo» → pide el ingreso; luego «gano ...» y «quiero un préstamo» de nuevo (condicional)
- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario E: Sin score registrado (R06): no se puede evaluar solo, ofrece derivar

**Carla** · documento `46395401` · Colombia · moneda COP · ingreso 9.000.000 COP · score sin score (banda sin banda) · mora máx. 0 días · marketing: sí · gold: `none`, motivos R06_SCORE_MISSING

- Ocupación registrada: **Jubilado/a**
- Año en que se hizo cliente: **2022**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - tarjeta de débito: apertura en sucursal de **Cartagena**, año **2021**
  - cuenta corriente: apertura en sucursal de **Barranquilla**, año **2024**
  - cuenta de ahorro: apertura en sucursal de **Cartagena**, año **2022**
- Documentos que el banco ya tiene: copia del documento de identidad (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario F: Mora de más de 30 días (R03): crédito rechazado

**Joaquín** · documento `70002780` · México · moneda MXN · ingreso 103.000 MXN · score 675 (banda C) · mora máx. 120 días · marketing: sí · gold: `none`, motivos R03_DELINQUENCY

- Ocupación registrada: **Docente**
- Año en que se hizo cliente: **2020**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - tarjeta de crédito: apertura en sucursal de **Puebla**, año **2023**
  - tarjeta de débito: apertura por App (no se pregunta la ciudad), año **2021**
  - cuenta corriente: apertura por Web (no se pregunta la ciudad), año **2025**
  - cuenta de ahorro: apertura por Web (no se pregunta la ciudad), año **2020**
- Documentos que el banco ya tiene: comprobante de domicilio, copia del documento de identidad, comprobante de ingresos (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario G: Sin capacidad de endeudamiento (R08): sus cuotas actuales ya llegan al 20% del ingreso

**Lucas** · documento `21523163` · México · moneda MXN · ingreso 25.000 MXN · score 619 (banda D) · mora máx. 0 días · marketing: no · gold: `none`, motivos R08_NO_CAPACITY

- Ocupación registrada: **Profesional independiente**
- Año en que se hizo cliente: **2022**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - tarjeta de débito: apertura en sucursal de **Ciudad de México**, año **2023**
  - cuenta corriente: apertura por Web (no se pregunta la ciudad), año **2023**
  - cuenta de ahorro: apertura por Web (no se pregunta la ciudad), año **2021**
- Documentos que el banco ya tiene: comprobante de domicilio, copia del documento de identidad, comprobante de ingresos (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario H: Score bajo el mínimo (banda E, R06): crédito rechazado

**Renata** · documento `77695536` · México · moneda MXN · ingreso 44.000 MXN · score 502 (banda E) · mora máx. 0 días · marketing: sí · gold: `none`, motivos R06_SCORE_BELOW_MIN

- Ocupación registrada: **Docente**
- Año en que se hizo cliente: **2024**
- Productos activos (año de apertura y, si se abrieron en sucursal, la ciudad; si hay varios del mismo tipo, el reto pregunta por el más antiguo):
  - cuenta corriente: apertura en sucursal de **Tijuana**, año **2018**
  - cuenta de ahorro: apertura por App (no se pregunta la ciudad), año **2019**
  - tarjeta de débito: apertura en sucursal de **Ciudad de México**, año **2025**
- Documentos que el banco ya tiene: comprobante de domicilio, copia del documento de identidad (al avanzar con una solicitud solo se piden los que faltan)

Frases para probar:

- «¿eres un robot?» → responde con la verdad: es el asistente virtual del banco, no una persona
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario I: sin datos suficientes para 3 preguntas (no se puede verificar por este canal)

Estos documentos reciben el aviso «No es posible verificar su identidad por este canal»: `50312198`

## Escenario J: documento inexistente

Cualquier número que no esté arriba (por ejemplo `99999999`) recibe preguntas igual que un cliente real, pero nunca se aprueban: así no se revela qué documentos existen.
