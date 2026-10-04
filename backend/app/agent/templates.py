"""Plantillas revisadas es/pt. Todo numero que ve el cliente entra por facts["fmt"] ya formateado.

Las plantillas son la fuente de verdad del texto con cifras; un LLM puede reescribirlas para sonar mas natural,
pero la respuesta final se descarta si contiene numeros que no estan en facts["fmt"] (ver orchestrator.safe_text).
"""
from __future__ import annotations

PRODUCT_NAME = {
    "es": {"personal_loan": "préstamo personal", "credit_card": "tarjeta de crédito", "mortgage": "préstamo hipotecario"},
    "pt": {"personal_loan": "empréstimo pessoal", "credit_card": "cartão de crédito", "mortgage": "financiamento imobiliário"},
}

REASON_TEXT = {
    "es": {"CUSTOMER_NOT_ACTIVE": "su cuenta no está activa", "DELINQUENT_REVIEW": "tiene pagos atrasados",
           "BORDERLINE_DTI": "su capacidad de pago está justo en el límite",
           "INCOME_UPLIFT_REVIEW": "el aumento de ingreso informado necesita verificación",
           "MISSING_DATA": "faltan datos para evaluarlo", "USER_REQUEST": "usted lo solicitó",
           "UNSUPPORTED_PRODUCT": "ese producto lo atiende un asesor", "UNCLEAR": "no pude entender su consulta", "DOCS_INCOMPLETE": "falta documentación por completar",
           "OTHER_TOPIC": "ese tema lo atiende un asesor"},
    "pt": {"CUSTOMER_NOT_ACTIVE": "sua conta não está ativa", "DELINQUENT_REVIEW": "há pagamentos em atraso",
           "BORDERLINE_DTI": "sua capacidade de pagamento está no limite",
           "INCOME_UPLIFT_REVIEW": "o aumento de renda informado precisa de verificação",
           "MISSING_DATA": "faltam dados para a análise", "USER_REQUEST": "você solicitou",
           "UNSUPPORTED_PRODUCT": "esse produto é atendido por um consultor", "UNCLEAR": "não consegui entender a consulta", "DOCS_INCOMPLETE": "falta documentação a completar",
           "OTHER_TOPIC": "esse assunto é atendido por um consultor"},
}

T: dict[str, dict[str, str]] = {
    "greeting": {
        "es": "Hola {first_name}, soy el asistente virtual del banco. Puedo mostrarle su oferta de crédito, calcular si califica para un monto y recalcular con los datos que usted me indique. ¿Qué necesita?",
        "pt": "Olá {first_name}, sou o assistente virtual do banco. Posso mostrar sua oferta de crédito, calcular se você se qualifica para um valor e recalcular com os dados que você informar. Do que você precisa?",
    },
    "thanks": {"es": "Con gusto. ¿Algo más en lo que pueda ayudarle?", "pt": "Por nada. Posso ajudar em mais alguma coisa?"},
    "unknown": {
        "es": "No estoy seguro de haberle entendido. Puedo ayudarle con ofertas de crédito, calcular si califica para un monto o derivarlo con un asesor. ¿Qué prefiere?",
        "pt": "Não tenho certeza se entendi. Posso ajudar com ofertas de crédito, calcular se você se qualifica para um valor ou encaminhar a um consultor. O que prefere?",
    },
    "ask_amount": {
        "es": "¿Qué monto necesita para su {product}? Si quiere, indique también el plazo en meses (por defecto uso {months}).",
        "pt": "Qual valor você precisa para o seu {product}? Se quiser, informe também o prazo em meses (por padrão uso {months}).",
    },
    "ask_income": {
        "es": "No tengo su ingreso mensual registrado. Si me indica su ingreso mensual aproximado ({ccy}), calculo de forma provisional; el resultado quedará sujeto a verificación.",
        "pt": "Não tenho sua renda mensal registrada. Se você informar sua renda mensal aproximada ({ccy}), calculo de forma provisória; o resultado ficará sujeito a verificação.",
    },
    "income_saved": {
        "es": "{fx}Anotado: ingreso mensual declarado de {income}. Queda sujeto a verificación. ¿Qué monto desea consultar?",
        "pt": "{fx}Anotado: renda mensal declarada de {income}. Fica sujeita a verificação. Qual valor deseja consultar?",
    },
    "eligible": {
        "es": "{fx}Con los datos del banco, su solicitud de {product} por {amount} a {months} meses es preliminarmente elegible. Cuota estimada: {payment} al mes, tasa anual de {rate}. Con esa cuota su endeudamiento sería de {dti} de su ingreso, bajo el máximo de {max_dti}. Es una simulación; la aprobación final requiere revisión del banco.",
        "pt": "{fx}Com os dados do banco, sua solicitação de {product} de {amount} em {months} meses é preliminarmente elegível. Parcela estimada: {payment} por mês, taxa anual de {rate}. Com essa parcela seu endividamento seria de {dti} da sua renda, abaixo do máximo de {max_dti}. É uma simulação; a aprovação final requer análise do banco.",
    },
    "eligible_provisional": {
        "es": "{fx}Con el ingreso que usted declaró, su solicitud de {product} por {amount} a {months} meses es preliminarmente elegible, sujeta a verificación de ingresos. Cuota estimada: {payment} al mes, tasa anual de {rate}; su endeudamiento sería de {dti}, bajo el máximo de {max_dti}. Es una simulación.",
        "pt": "{fx}Com a renda que você declarou, sua solicitação de {product} de {amount} em {months} meses é preliminarmente elegível, sujeita à verificação de renda. Parcela estimada: {payment} por mês, taxa anual de {rate}; seu endividamento seria de {dti}, abaixo do máximo de {max_dti}. É uma simulação.",
    },
    "declined_dti": {
        "es": "{fx}Con esa cuota su endeudamiento sería de {dti}, por encima del máximo de {max_dti}. Con su situación actual, el monto máximo estimado a {months} meses sería de {max_amount}. Si sus ingresos cambiaron, indíquemelo y recalculo.",
        "pt": "{fx}Com essa parcela seu endividamento seria de {dti}, acima do máximo de {max_dti}. Na sua situação atual, o valor máximo estimado em {months} meses seria de {max_amount}. Se sua renda mudou, me informe e eu recalculo.",
    },
    "declined_no_capacity": {
        "es": "{fx}Con esa cuota su endeudamiento sería de {dti}, por encima del máximo de {max_dti}, y con sus compromisos actuales no tiene capacidad de endeudamiento disponible por ahora. Si sus ingresos cambiaron, indíquemelo y recalculo.",
        "pt": "{fx}Com essa parcela seu endividamento seria de {dti}, acima do máximo de {max_dti}, e com seus compromissos atuais você não tem capacidade de endividamento disponível por enquanto. Se sua renda mudou, me informe e eu recalculo.",
    },
    "declined_generic": {
        "es": "Por ahora no es posible ofrecerle este crédito según las políticas del banco. Puedo derivarlo con un asesor si desea revisar su caso. ¿Lo derivo?",
        "pt": "No momento não é possível oferecer este crédito segundo as políticas do banco. Posso encaminhá-lo a um consultor para revisar seu caso. Encaminho?",
    },
    "needs_review": {
        "es": "Su caso requiere revisión de un asesor porque {reason}. ¿Quiere que lo derive ahora?",
        "pt": "Seu caso requer análise de um consultor porque {reason}. Quer que eu encaminhe agora?",
    },
    "needs_data_score": {
        "es": "No tengo información suficiente para evaluarlo automáticamente. Un asesor puede ayudarle. ¿Lo derivo?",
        "pt": "Não tenho informações suficientes para avaliar automaticamente. Um consultor pode ajudar. Encaminho?",
    },
    "unsupported_product": {
        "es": "Las tarjetas de crédito las gestiona un asesor. ¿Quiere que lo derive?",
        "pt": "Os cartões de crédito são tratados por um consultor. Quer que eu encaminhe?",
    },
    "offers": {
        "es": "Referencias de tasa anual para su perfil: {lines}. {capacity}",
        "pt": "Referências de taxa anual para o seu perfil: {lines}. {capacity}",
    },
    "offers_capacity": {
        "es": "Con su situación actual, un préstamo personal a {months} meses podría llegar a un máximo estimado de {max_amount}. Es una simulación.",
        "pt": "Na sua situação atual, um empréstimo pessoal em {months} meses poderia chegar a um máximo estimado de {max_amount}. É uma simulação.",
    },
    "offers_no_capacity": {
        "es": "Para estimar un monto máximo necesito más datos; puede indicarme su ingreso mensual.",
        "pt": "Para estimar um valor máximo preciso de mais dados; você pode me informar sua renda mensal.",
    },
    "income_review": {
        "es": "El ingreso que indicó supera de forma importante el que tenemos registrado, así que necesita verificación de un asesor. ¿Lo derivo?",
        "pt": "A renda informada supera de forma importante a registrada, então precisa de verificação de um consultor. Encaminho?",
    },
    "handoff_created": {
        "es": "Listo, derivé su caso a un asesor con el resumen de esta conversación. Su número de seguimiento es {ticket}. No tendrá que repetir la información.",
        "pt": "Pronto, encaminhei seu caso a um consultor com o resumo desta conversa. Seu número de acompanhamento é {ticket}. Você não precisará repetir as informações.",
    },
    "handoff_declined": {
        "es": "De acuerdo, no lo derivo. ¿Desea consultar otro monto o plazo?",
        "pt": "Certo, não vou encaminhar. Deseja consultar outro valor ou prazo?",
    },
    "handoff_exists": {
        "es": "Su caso ya fue derivado a un asesor con el número {ticket}.",
        "pt": "Seu caso já foi encaminhado a um consultor com o número {ticket}.",
    },
    "fx_note": {
        "es": "Convertí {src_amount} a {dst_amount} con la tasa de referencia del {date} ({rate}); no es una cotización en vivo.",
        "pt": "Converti {src_amount} para {dst_amount} com a taxa de referência de {date} ({rate}); não é uma cotação em tempo real.",
    },
    "currency_unsupported": {
        "es": "Todavía no puedo trabajar con {ccy}: manejo {supported}. ¿Me indica el monto en alguna de esas monedas?",
        "pt": "Ainda não consigo trabalhar com {ccy}: trabalho com {supported}. Pode me informar o valor em uma dessas moedas?",
    },
    "fx_unavailable": {
        "es": "En este momento no tengo una tasa de cambio disponible de {ccy} a su moneda. ¿Me indica el monto en su moneda local?",
        "pt": "No momento não tenho uma taxa de câmbio disponível de {ccy} para a sua moeda. Pode me informar o valor na sua moeda local?",
    },
    "ask_proceed": {
        "es": "¿Le gustaría que avancemos con la solicitud?",
        "pt": "Gostaria que avançássemos com a solicitação?",
    },
    "proceed_declined": {
        "es": "Sin problema. Si más adelante quiere retomarla, aquí estaré.",
        "pt": "Sem problema. Se mais adiante quiser retomar, estarei por aqui.",
    },
    "docs_request": {
        "es": "{lead}Para avanzar necesito que tenga a la mano: {docs}. ¿Cuenta con todos?",
        "pt": "{lead}Para avançar preciso que você tenha em mãos: {docs}. Você tem todos?",
    },
    "docs_item": {
        "es": "{lead}¿Cuenta con {doc}?",
        "pt": "{lead}Você tem {doc}?",
    },
    "application_ready": {
        "es": "Perfecto, ya tengo todo lo necesario. Pasé su solicitud a un asesor para la revisión final; su número de seguimiento es {ticket}. No tendrá que repetirle lo que ya me contó.",
        "pt": "Perfeito, já tenho tudo o que preciso. Encaminhei sua solicitação a um consultor para a revisão final; seu número de acompanhamento é {ticket}. Você não precisará repetir o que já me contou.",
    },
    "docs_incomplete": {
        "es": "Todavía me falta: {missing}. Puede enviarlo respondiendo al correo con el resumen, o llevarlo a una sucursal. ¿Quiere que un asesor lo contacte para ver cómo avanzar?",
        "pt": "Ainda falta: {missing}. Você pode enviar respondendo ao e-mail com o resumo, ou levar a uma agência. Quer que um consultor entre em contato para ver como avançar?",
    },
    "closing_summary": {
        "es": "Con gusto. Antes de despedirnos, le dejo el resumen de su propuesta:",
        "pt": "Com prazer. Antes de nos despedirmos, deixo o resumo da sua proposta:",
    },
    "summary": {
        "es": "• Producto: {product}\n• Monto: {amount}\n• Plazo: {months} meses\n• Tasa anual: {rate}\n• Cuota mensual estimada: {payment}\n• Endeudamiento con la cuota: {dti} de su ingreso (máximo {max_dti})\n• Estado: {status}\n• Documentación: {docs_status}{ticket_line}",
        "pt": "• Produto: {product}\n• Valor: {amount}\n• Prazo: {months} meses\n• Taxa anual: {rate}\n• Parcela mensal estimada: {payment}\n• Endividamento com a parcela: {dti} da sua renda (máximo {max_dti})\n• Situação: {status}\n• Documentação: {docs_status}{ticket_line}",
    },
    "email_notice": {
        "es": "Le enviaremos el detalle completo en un PDF al correo registrado ({email}). Es un resumen informativo: la aprobación final depende de la verificación del banco.",
        "pt": "Enviaremos o detalhe completo em um PDF para o e-mail cadastrado ({email}). É um resumo informativo: a aprovação final depende da verificação do banco.",
    },
    "email_notice_noaddr": {
        "es": "Le enviaremos el detalle completo en un PDF al correo que tenemos registrado. Es un resumen informativo: la aprobación final depende de la verificación del banco.",
        "pt": "Enviaremos o detalhe completo em um PDF para o e-mail que temos cadastrado. É um resumo informativo: a aprovação final depende da verificação do banco.",
    },
    "goodbye": {
        "es": "Quedo atento por si necesita algo más. ¡Que tenga un excelente día!",
        "pt": "Fico à disposição se precisar de mais alguma coisa. Tenha um ótimo dia!",
    },
    "closing": {
        "es": "Con gusto, que tenga un buen día.",
        "pt": "Por nada, tenha um bom dia.",
    },
    "other_topic": {
        "es": "Ese tema lo atiende un asesor. ¿Quiere que lo derive ahora?",
        "pt": "Esse assunto é atendido por um consultor. Quer que eu encaminhe agora?",
    },
    "offer_proactive": {
        "es": "Por cierto, {first_name}: según los datos del banco tiene una preaprobación indicativa de un {product} de hasta {max_amount} a {months} meses, con tasa anual de {rate}. Es una simulación, sujeta a verificación y aprobación final. ¿Le interesa conocer los detalles?",
        "pt": "A propósito, {first_name}: com base nos dados do banco você tem uma pré-aprovação indicativa de um {product} de até {max_amount} em {months} meses, com taxa anual de {rate}. É uma simulação, sujeita a verificação e aprovação final. Você tem interesse em saber os detalhes?",
    },
    "offer_accepted": {
        "es": "Perfecto. ¿Qué monto necesita? Puede indicar también el plazo en meses (por defecto uso {months}).",
        "pt": "Perfeito. Qual valor você precisa? Você também pode informar o prazo em meses (por padrão uso {months}).",
    },
    "offer_declined": {
        "es": "Entendido, no se lo volveré a proponer en esta conversación. ¿Puedo ayudarle en algo más?",
        "pt": "Entendido, não vou propor novamente nesta conversa. Posso ajudar em mais alguma coisa?",
    },
}

DOC_NAME = {
    "es": {"id_copy": "su copia del documento de identidad", "address_proof": "su comprobante de domicilio",
           "income_proof": "su comprobante de ingresos", "bank_statements_3m": "sus estados de cuenta de los últimos 3 meses",
           "property_deed": "la escritura de la propiedad", "appraisal": "el avalúo de la propiedad"},
    "pt": {"id_copy": "sua cópia do documento de identidade", "address_proof": "seu comprovante de endereço",
           "income_proof": "seu comprovante de renda", "bank_statements_3m": "seus extratos bancários dos últimos 3 meses",
           "property_deed": "a escritura do imóvel", "appraisal": "a avaliação do imóvel"},
}
SUMMARY_TEXT = {
    "es": {"eligible": "preliminarmente elegible", "provisional": "preliminarmente elegible, sujeta a verificación de ingresos",
           "docs_complete": "completa; la revisará un asesor", "docs_pending": "pendiente: {missing}", "docs_not_started": "aún sin iniciar",
           "ticket_line": "\n• Seguimiento: {ticket}"},
    "pt": {"eligible": "preliminarmente elegível", "provisional": "preliminarmente elegível, sujeita à verificação de renda",
           "docs_complete": "completa; um consultor fará a revisão", "docs_pending": "pendente: {missing}", "docs_not_started": "ainda não iniciada",
           "ticket_line": "\n• Acompanhamento: {ticket}"},
}
REASK = {"es": "Perdone, no le entendí bien. ", "pt": "Desculpe, não entendi bem. "}
SUMMARY_LABELS = {
    "es": [("product", "Producto"), ("amount", "Monto"), ("months_text", "Plazo"), ("rate", "Tasa anual"),
           ("payment", "Cuota mensual estimada"), ("dti_text", "Endeudamiento con la cuota"), ("status", "Estado"),
           ("docs_status", "Documentación")],
    "pt": [("product", "Produto"), ("amount", "Valor"), ("months_text", "Prazo"), ("rate", "Taxa anual"),
           ("payment", "Parcela mensal estimada"), ("dti_text", "Endividamento com a parcela"), ("status", "Situação"),
           ("docs_status", "Documentação")],
}
PDF_NOTES = {
    "es": ["Simulación con datos y política sintéticos: no constituye una oferta ni una aprobación de crédito.",
           "Las cifras están sujetas a la verificación de ingresos y documentos y a la aprobación final del banco."],
    "pt": ["Simulação com dados e política sintéticos: não constitui uma oferta nem uma aprovação de crédito.",
           "Os valores estão sujeitos à verificação de renda e documentos e à aprovação final do banco."],
}
EMAIL_SUBJECT = {"es": "Resumen de su propuesta de crédito", "pt": "Resumo da sua proposta de crédito"}

SUGGESTIONS = {
    "yes_no": {"es": ["Sí", "No"], "pt": ["Sim", "Não"]},
    "start": {"es": ["Ver mi oferta de crédito", "Quiero un préstamo", "Hablar con un asesor"],
              "pt": ["Ver minha oferta de crédito", "Quero um empréstimo", "Falar com um consultor"]},
}


def render(kind: str, lang: str, fmt: dict[str, str]) -> str:
    return T[kind][lang].format(**{"fx": "", **fmt})


def render_facts(facts: dict, lang: str) -> str:
    """Texto base + (opcional) un segundo mensaje en la misma linea (p. ej. la pregunta de seguir) + bloques aparte."""
    text = render(facts["kind"], lang, facts["fmt"])
    if facts.get("kind2"):
        text += " " + render(facts["kind2"], lang, facts["fmt"])
    for extra in facts.get("extras", []):
        text += "\n\n" + render(extra, lang, facts["fmt"])
    return text


def join_list(items: list[str], lang: str) -> str:
    """'a, b y c' / 'a, b e c'."""
    conj = {"es": "y", "pt": "e"}[lang]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} {items[-1]}"
