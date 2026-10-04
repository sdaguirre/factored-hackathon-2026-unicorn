# Datos de prueba del asistente

Todo es **sintético**; no hay personas reales. Se genera con `python scripts/gen_test_data.py` a partir de los datos que use el backend (`data/fixture/` por defecto).

## Cómo probar

1. Abra la interfaz (`http://localhost:8080`) y escriba el **documento** de un cliente de abajo.
2. Responda las **preguntas de seguridad** con la ficha del cliente. Las preguntas cambian en cada intento y salen de estos mismos datos: ciudad o mes y año de apertura de un producto (se identifica por su terminación), y ciudad o monto de un movimiento (se identifica por su fecha y tipo). Las opciones incorrectas son inventadas.
3. Pruebe las frases sugeridas de cada escenario. Para portugués, cambie el selector de idioma antes de empezar.

Tres intentos fallidos bloquean ese documento 15 minutos (también un documento inexistente, a propósito). Para desbloquear, reinicie el backend: `docker compose restart chat-backend`.

Los montos de los ejemplos están en la moneda del ingreso de cada cliente. Los resultados indicados (eligible, declined…) son los de la política provisional con los datos de hoy.

## Escenario A: Consiente marketing y está preaprobado: recibe la oferta proactiva al cerrar

**Bruno** · documento `53464097` · México · moneda MXN · ingreso 95.000 MXN · score 740 · mora máx. 0 días · marketing: sí

- Productos (ciudad y mes de apertura):
  - cuenta de ahorro con terminación **9313**: abierta en **Tijuana**, en **abril de 2022**
  - tarjeta de crédito con terminación **4517**: abierta en **Querétaro**, en **agosto de 2024**
  - tarjeta de débito con terminación **1614**: abierta en **Monterrey**, en **enero de 2019**
- Movimientos de los últimos 12 meses:
  - 06/05/2026, retiro: ciudad **Guadalajara**, monto **1.636,75 MXN**
  - 04/05/2026, retiro: ciudad **Guadalajara**, monto **2.462,27 MXN**
  - 28/04/2026, compra: ciudad **Guadalajara**, monto **2.942,12 MXN**
  - 22/03/2026, compra: ciudad **Guadalajara**, monto **1.767,69 MXN**
  - 14/03/2026, compra: ciudad **Guadalajara**, monto **2.014,79 MXN**
  - 17/12/2025, retiro: ciudad **Guadalajara**, monto **3.186,43 MXN**
  - 23/11/2025, compra: ciudad **Ciudad de México**, monto **2.376,75 MXN**
  - 12/11/2025, compra: ciudad **Guadalajara**, monto **2.841,25 MXN**
  - 28/10/2025, compra: ciudad **Guadalajara**, monto **5.307,53 MXN**
  - 25/10/2025, compra: ciudad **Guadalajara**, monto **1.309,98 MXN**
  - 03/10/2025, retiro: ciudad **Guadalajara**, monto **1.807,95 MXN**
  - 07/09/2025, retiro: ciudad **Guadalajara**, monto **1.391,86 MXN**

Frases para probar:

- «¿qué tasas tienen para mí?»
- «gracias, eso es todo» (debe llegar la oferta)
- «quiero un préstamo de 28500 a 24 meses» → eligible
- «necesito un préstamo de 570000» → declined
- «ahora gano 133000 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

**Daniel** · documento `59689823` · Colombia · moneda COP · ingreso 10.500.000 COP · score 613 · mora máx. 0 días · marketing: sí

- Productos (ciudad y mes de apertura):
  - cuenta de ahorro con terminación **7493**: abierta en **Medellín**, en **noviembre de 2022**
  - tarjeta de débito con terminación **4245**: abierta en **Medellín**, en **abril de 2022**
  - cuenta corriente con terminación **1110**: abierta en **Cali**, en **septiembre de 2025**
- Movimientos de los últimos 12 meses:
  - 30/05/2026, retiro: ciudad **Medellín**, monto **289.652,74 COP**
  - 19/05/2026, retiro: ciudad **Medellín**, monto **261.650,95 COP**
  - 12/04/2026, retiro: ciudad **Medellín**, monto **77.405,18 COP**
  - 01/04/2026, compra: ciudad **Medellín**, monto **493.501,99 COP**
  - 20/03/2026, compra: ciudad **Medellín**, monto **95.328,48 COP**
  - 09/03/2026, compra: ciudad **Medellín**, monto **120.990,88 COP**
  - 07/01/2026, compra: ciudad **Medellín**, monto **218.670,26 COP**
  - 06/01/2026, retiro: ciudad **Medellín**, monto **323.196,05 COP**
  - 05/01/2026, retiro: ciudad **Medellín**, monto **372.516,19 COP**
  - 10/09/2025, compra: ciudad **Medellín**, monto **152.978,06 COP**
  - 06/08/2025, compra: ciudad **Barranquilla**, monto **214.075,63 COP**

Frases para probar:

- «¿qué tasas tienen para mí?»
- «gracias, eso es todo» (debe llegar la oferta)
- «quiero un préstamo de 3150000 a 24 meses» → eligible
- «necesito un préstamo de 63000000» → declined
- «ahora gano 14699999 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

**Valentín** · documento `11542972` · Argentina · moneda ARS · ingreso 586.000 ARS · score 565 · mora máx. 0 días · marketing: sí

- Productos (ciudad y mes de apertura):
  - tarjeta de débito con terminación **2008**: abierta en **Córdoba**, en **enero de 2021**
  - tarjeta de crédito con terminación **9708**: abierta en **Mendoza**, en **marzo de 2020**
  - cuenta corriente con terminación **1413**: abierta en **Rosario**, en **junio de 2018**
  - cuenta de ahorro con terminación **7651**: abierta en **Rosario**, en **noviembre de 2018**
- Movimientos de los últimos 12 meses:
  - 11/06/2026, compra: ciudad **Buenos Aires**, monto **9.028,02 ARS**
  - 07/06/2026, compra: ciudad **Buenos Aires**, monto **11.696,39 ARS**
  - 04/05/2026, retiro: ciudad **Buenos Aires**, monto **9.658,11 ARS**
  - 01/04/2026, compra: ciudad **Buenos Aires**, monto **9.793,32 ARS**
  - 25/03/2026, retiro: ciudad **Córdoba**, monto **12.237,28 ARS**
  - 26/02/2026, compra: ciudad **Buenos Aires**, monto **39.522,69 ARS**
  - 12/02/2026, compra: ciudad **La Plata**, monto **6.406,39 ARS**
  - 01/02/2026, compra: ciudad **Buenos Aires**, monto **12.042,34 ARS**
  - 31/01/2026, retiro: ciudad **Buenos Aires**, monto **10.467,78 ARS**
  - 07/01/2026, compra: ciudad **Buenos Aires**, monto **26.153,77 ARS**
  - 20/09/2025, compra: ciudad **Buenos Aires**, monto **14.720,13 ARS**
  - 12/09/2025, compra: ciudad **Buenos Aires**, monto **14.767,59 ARS**

Frases para probar:

- «¿qué tasas tienen para mí?»
- «gracias, eso es todo» (debe llegar la oferta)
- «quiero un préstamo de 175800 a 24 meses» → eligible
- «necesito un préstamo de 3516000» → declined
- «ahora gano 820400 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario B: NO consiente marketing pero está preaprobado: puede pedir crédito, nunca recibe oferta proactiva

**Valentín** · documento `31667923` · México · moneda MXN · ingreso 80.000 MXN · score 756 · mora máx. 0 días · marketing: no

- Productos (ciudad y mes de apertura):
  - cuenta corriente con terminación **1443**: abierta en **Ciudad de México**, en **diciembre de 2019**
  - tarjeta de débito con terminación **9652**: abierta en **Puebla**, en **octubre de 2025**
  - tarjeta de crédito con terminación **5883**: abierta en **Querétaro**, en **marzo de 2024**
- Movimientos de los últimos 12 meses:
  - 31/05/2026, compra: ciudad **Tijuana**, monto **2.197,23 MXN**
  - 02/05/2026, retiro: ciudad **Tijuana**, monto **1.942,64 MXN**
  - 10/04/2026, compra: ciudad **Tijuana**, monto **1.501,55 MXN**
  - 04/03/2026, compra: ciudad **Puebla**, monto **2.140,70 MXN**
  - 22/02/2026, compra: ciudad **Tijuana**, monto **441,80 MXN**
  - 24/11/2025, compra: ciudad **Tijuana**, monto **798,57 MXN**
  - 21/11/2025, retiro: ciudad **Guadalajara**, monto **2.353,51 MXN**
  - 09/08/2025, compra: ciudad **Tijuana**, monto **1.787,81 MXN**
  - 30/07/2025, retiro: ciudad **Puebla**, monto **1.447,64 MXN**

Frases para probar:

- «¿qué tasas tienen para mí?»
- «gracias, eso es todo» (NO debe llegar oferta)
- «quiero un préstamo de 24000 a 24 meses» → eligible
- «necesito un préstamo de 480000» → declined
- «ahora gano 112000 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

**Bruno** · documento `41532215` · Colombia · moneda COP · ingreso 5.800.000 COP · score 640 · mora máx. 0 días · marketing: no

- Productos (ciudad y mes de apertura):
  - cuenta de ahorro con terminación **5265**: abierta en **Medellín**, en **junio de 2024**
  - cuenta corriente con terminación **4263**: abierta en **Medellín**, en **abril de 2021**
  - tarjeta de débito con terminación **8199**: abierta en **Medellín**, en **mayo de 2022**
  - tarjeta de crédito con terminación **5053**: abierta en **Cartagena**, en **febrero de 2021**
- Movimientos de los últimos 12 meses:
  - 29/05/2026, compra: ciudad **Cartagena**, monto **86.657,18 COP**
  - 20/05/2026, compra: ciudad **Cartagena**, monto **77.985,92 COP**
  - 15/03/2026, compra: ciudad **Cartagena**, monto **138.272,30 COP**
  - 02/03/2026, compra: ciudad **Cartagena**, monto **77.018,29 COP**
  - 17/11/2025, retiro: ciudad **Cartagena**, monto **169.463,44 COP**
  - 14/10/2025, compra: ciudad **Cali**, monto **198.081,45 COP**
  - 06/09/2025, compra: ciudad **Cartagena**, monto **259.816,02 COP**
  - 01/08/2025, compra: ciudad **Cartagena**, monto **148.102,69 COP**

Frases para probar:

- «¿qué tasas tienen para mí?»
- «gracias, eso es todo» (NO debe llegar oferta)
- «quiero un préstamo de 1740000 a 24 meses» → eligible
- «necesito un préstamo de 34800000» → declined
- «ahora gano 8119999 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario C: Sin ingreso registrado: el asistente pide que lo declare (queda provisional)

**Alicia** · documento `52410090` · México · moneda MXN · ingreso sin ingreso registrado · score 790 · mora máx. 0 días · marketing: no

- Productos (ciudad y mes de apertura):
  - cuenta corriente con terminación **6999**: abierta en **Monterrey**, en **julio de 2025**
  - tarjeta de crédito con terminación **3342**: abierta en **Monterrey**, en **agosto de 2021**
  - cuenta de ahorro con terminación **5146**: abierta en **Tijuana**, en **septiembre de 2019**
  - tarjeta de débito con terminación **3248**: abierta en **Puebla**, en **enero de 2020**
- Movimientos de los últimos 12 meses:
  - 23/05/2026, retiro: ciudad **Ciudad de México**, monto **39,50 MXN**
  - 20/05/2026, compra: ciudad **Ciudad de México**, monto **12,17 MXN**
  - 16/05/2026, retiro: ciudad **Ciudad de México**, monto **26,86 MXN**
  - 01/03/2026, compra: ciudad **Ciudad de México**, monto **26,14 MXN**
  - 06/02/2026, compra: ciudad **Ciudad de México**, monto **35,34 MXN**
  - 30/01/2026, compra: ciudad **Ciudad de México**, monto **56,50 MXN**
  - 14/01/2026, retiro: ciudad **Ciudad de México**, monto **26,17 MXN**
  - 10/11/2025, retiro: ciudad **Monterrey**, monto **9,88 MXN**
  - 25/10/2025, compra: ciudad **Ciudad de México**, monto **83,02 MXN**
  - 23/10/2025, compra: ciudad **Ciudad de México**, monto **11,99 MXN**
  - 24/09/2025, retiro: ciudad **Ciudad de México**, monto **41,87 MXN**
  - 25/07/2025, compra: ciudad **Ciudad de México**, monto **17,01 MXN**

Frases para probar:

- «quiero un préstamo de 3000» → pide el ingreso; luego «gano 5000»
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario D: Sin score registrado: no se puede evaluar solo, ofrece derivar

**Carla** · documento `46395401` · Colombia · moneda COP · ingreso 9.000.000 COP · score sin score · mora máx. 0 días · marketing: sí

- Productos (ciudad y mes de apertura):
  - tarjeta de débito con terminación **7784**: abierta en **Cartagena**, en **septiembre de 2021**
  - cuenta corriente con terminación **7823**: abierta en **Barranquilla**, en **junio de 2024**
  - cuenta de ahorro con terminación **1298**: abierta en **Cartagena**, en **marzo de 2022**
- Movimientos de los últimos 12 meses:
  - 24/05/2026, retiro: ciudad **Bogotá**, monto **78.920,62 COP**
  - 15/04/2026, compra: ciudad **Barranquilla**, monto **354.299,82 COP**
  - 03/04/2026, retiro: ciudad **Barranquilla**, monto **237.385,24 COP**
  - 24/03/2026, retiro: ciudad **Barranquilla**, monto **209.544,16 COP**
  - 13/03/2026, retiro: ciudad **Barranquilla**, monto **62.737,55 COP**
  - 21/02/2026, retiro: ciudad **Barranquilla**, monto **279.724,25 COP**
  - 10/12/2025, compra: ciudad **Barranquilla**, monto **252.585,03 COP**
  - 07/12/2025, retiro: ciudad **Barranquilla**, monto **157.824,46 COP**
  - 30/11/2025, compra: ciudad **Barranquilla**, monto **373.928,84 COP**
  - 11/11/2025, compra: ciudad **Barranquilla**, monto **135.477,04 COP**
  - 06/10/2025, compra: ciudad **Barranquilla**, monto **67.860,40 COP**
  - 07/08/2025, retiro: ciudad **Barranquilla**, monto **114.120,62 COP**

Frases para probar:

- «quiero un préstamo de 2700000 a 24 meses» → needs_data
- «necesito un préstamo de 54000000» → needs_data
- «ahora gano 12600000 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario E: Mora de 31 a 90 días: va a revisión de un asesor

**Héctor** · documento `44325214` · México · moneda MXN · ingreso 70.000 MXN · score 658 · mora máx. 45 días · marketing: no

- Productos (ciudad y mes de apertura):
  - tarjeta de débito con terminación **2961**: abierta en **Querétaro**, en **julio de 2023**
  - cuenta corriente con terminación **3741**: abierta en **Monterrey**, en **mayo de 2024**
  - cuenta de ahorro con terminación **3648**: abierta en **Querétaro**, en **julio de 2025**
  - tarjeta de crédito con terminación **2231**: abierta en **Puebla**, en **agosto de 2022**
- Movimientos de los últimos 12 meses:
  - 08/05/2026, compra: ciudad **Tijuana**, monto **1.341,07 MXN**
  - 10/04/2026, compra: ciudad **Querétaro**, monto **1.737,94 MXN**
  - 20/02/2026, compra: ciudad **Querétaro**, monto **3.522,93 MXN**
  - 15/02/2026, retiro: ciudad **Querétaro**, monto **1.724,83 MXN**
  - 07/02/2026, compra: ciudad **Querétaro**, monto **896,89 MXN**
  - 06/02/2026, retiro: ciudad **Querétaro**, monto **2.613,38 MXN**
  - 23/01/2026, retiro: ciudad **Guadalajara**, monto **1.554,64 MXN**
  - 23/10/2025, compra: ciudad **Querétaro**, monto **2.589,51 MXN**
  - 19/09/2025, compra: ciudad **Querétaro**, monto **1.627,68 MXN**
  - 27/08/2025, compra: ciudad **Querétaro**, monto **2.162,29 MXN**

Frases para probar:

- «quiero un préstamo de 21000 a 24 meses» → needs_review
- «necesito un préstamo de 420000» → needs_review
- «ahora gano 98000 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario F: Mora de más de 90 días: crédito rechazado

**Joaquín** · documento `70002780` · México · moneda MXN · ingreso 103.000 MXN · score 675 · mora máx. 120 días · marketing: sí

- Productos (ciudad y mes de apertura):
  - tarjeta de crédito con terminación **4761**: abierta en **Puebla**, en **octubre de 2023**
  - tarjeta de débito con terminación **6614**: abierta en **Ciudad de México**, en **septiembre de 2021**
  - cuenta corriente con terminación **4254**: abierta en **Guadalajara**, en **octubre de 2025**
  - cuenta de ahorro con terminación **3289**: abierta en **Tijuana**, en **septiembre de 2020**
- Movimientos de los últimos 12 meses:
  - 12/06/2026, compra: ciudad **Ciudad de México**, monto **6.104,11 MXN**
  - 24/05/2026, compra: ciudad **Ciudad de México**, monto **1.716,78 MXN**
  - 23/02/2026, compra: ciudad **Ciudad de México**, monto **2.474,25 MXN**
  - 31/01/2026, compra: ciudad **Tijuana**, monto **3.879,07 MXN**
  - 23/01/2026, retiro: ciudad **Ciudad de México**, monto **3.842,79 MXN**
  - 15/01/2026, compra: ciudad **Ciudad de México**, monto **2.299,24 MXN**
  - 04/10/2025, compra: ciudad **Ciudad de México**, monto **1.950,07 MXN**
  - 25/09/2025, retiro: ciudad **Ciudad de México**, monto **2.779,33 MXN**
  - 19/08/2025, retiro: ciudad **Guadalajara**, monto **2.908,78 MXN**

Frases para probar:

- «quiero un préstamo de 30900 a 24 meses» → declined
- «necesito un préstamo de 618000» → declined
- «ahora gano 144200 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario G: Sin capacidad de endeudamiento (su deuda actual ya supera el 20% del ingreso)

**Lucas** · documento `21523163` · México · moneda MXN · ingreso 25.000 MXN · score 619 · mora máx. 0 días · marketing: no

- Productos (ciudad y mes de apertura):
  - tarjeta de débito con terminación **7338**: abierta en **Ciudad de México**, en **octubre de 2023**
  - cuenta corriente con terminación **4437**: abierta en **Puebla**, en **marzo de 2023**
  - cuenta de ahorro con terminación **4452**: abierta en **Monterrey**, en **abril de 2021**
- Movimientos de los últimos 12 meses:
  - 14/04/2026, compra: ciudad **Puebla**, monto **589,56 MXN**
  - 09/03/2026, compra: ciudad **Puebla**, monto **537,88 MXN**
  - 06/03/2026, retiro: ciudad **Puebla**, monto **1.328,71 MXN**
  - 25/01/2026, compra: ciudad **Puebla**, monto **513,59 MXN**
  - 06/12/2025, compra: ciudad **Puebla**, monto **945,20 MXN**
  - 22/11/2025, retiro: ciudad **Puebla**, monto **1.559,11 MXN**
  - 19/11/2025, compra: ciudad **Puebla**, monto **473,36 MXN**
  - 16/11/2025, compra: ciudad **Puebla**, monto **483,44 MXN**
  - 09/10/2025, compra: ciudad **Puebla**, monto **189,10 MXN**
  - 06/09/2025, compra: ciudad **Puebla**, monto **520,89 MXN**
  - 24/07/2025, compra: ciudad **Puebla**, monto **501,00 MXN**

Frases para probar:

- «quiero un préstamo de 7500 a 24 meses» → declined
- «necesito un préstamo de 150000» → declined
- «ahora gano 35000 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario H: Score muy bajo (banda 1): crédito rechazado

**Renata** · documento `77695536` · México · moneda MXN · ingreso 44.000 MXN · score 502 · mora máx. 0 días · marketing: sí

- Productos (ciudad y mes de apertura):
  - cuenta corriente con terminación **6909**: abierta en **Tijuana**, en **noviembre de 2018**
  - cuenta de ahorro con terminación **2718**: abierta en **Querétaro**, en **noviembre de 2019**
  - tarjeta de débito con terminación **7170**: abierta en **Ciudad de México**, en **marzo de 2025**
- Movimientos de los últimos 12 meses:
  - 07/04/2026, compra: ciudad **Tijuana**, monto **497,18 MXN**
  - 23/02/2026, retiro: ciudad **Tijuana**, monto **682,85 MXN**
  - 30/01/2026, compra: ciudad **Tijuana**, monto **777,03 MXN**
  - 27/12/2025, compra: ciudad **Tijuana**, monto **2.186,81 MXN**
  - 22/10/2025, compra: ciudad **Ciudad de México**, monto **977,84 MXN**
  - 20/10/2025, compra: ciudad **Tijuana**, monto **1.621,66 MXN**
  - 18/09/2025, compra: ciudad **Tijuana**, monto **539,56 MXN**
  - 03/08/2025, retiro: ciudad **Tijuana**, monto **1.886,21 MXN**

Frases para probar:

- «quiero un préstamo de 13200 a 24 meses» → declined
- «necesito un préstamo de 264000» → declined
- «ahora gano 61599 al mes» (tras un rechazo por capacidad, recalcula como provisional)
- «no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito
- «quiero hablar con un asesor» → deriva y muestra el número de seguimiento

## Escenario I: sin datos suficientes para 3 preguntas (no se puede verificar por este canal)

Estos documentos reciben el aviso «No es posible verificar su identidad por este canal»: `50312198`

## Escenario J: documento inexistente

Cualquier número que no esté arriba (por ejemplo `99999999`) recibe preguntas igual que un cliente real, pero nunca se aprueban: así no se revela qué documentos existen.
