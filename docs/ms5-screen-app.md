# MS-5 — o que ainda falta no Screen-App

## Situação

O Payload CMS **não está no repositório MNScr**. Ele vive no Screen-App, junto com
o Cinerie — que é parte desse projeto, não um repositório separado.

Por isso a MS-5 entregou **apenas o lado MNScr**: contrato, validação, máquina de
estados, idempotência, retry, persistência e adapter HTTP. Tudo foi exercitado
contra um **servidor local falso** (`tests/fake_payload_server.py`, ligado apenas
a `127.0.0.1`).

**Nada aqui foi validado contra uma instância real de Payload.** O que segue é a
lista objetiva do que o Screen-App precisa confirmar ou implementar para a
integração funcionar de verdade.

## Três níveis de integração, para não confundir

| Nível | Estado |
|---|---|
| Implementada no MNScr | ✅ contrato, adapter, retry, idempotência, persistência |
| Simulada em teste | ✅ servidor local falso, 12 códigos HTTP, timeouts, retry, replay |
| Validada contra Payload real | ❌ **não feita** — depende dos itens abaixo |
| Trabalho restante no Screen-App | ❌ **não iniciado** — este documento |

## O que o MNScr envia hoje

`POST {PAYLOAD_CMS_BASE_URL}/api/{PAYLOAD_CMS_COLLECTION}`

```
Content-Type: application/json
Accept: application/json
Authorization: Bearer <PAYLOAD_CMS_TOKEN>
Idempotency-Key: <sha256 de draft_id + content_hash + destination>
```

Corpo: o contrato `mnscr-editorial-delivery` versão `1` (ver
`app/delivery/contract.py`), acrescido de `_status: "draft"`.

Resposta esperada: `2xx` com um identificador em `id`, `_id`, `docId` ou
`documentId` — no topo ou dentro de `doc` / `document` / `data` / `result`.

## Decisões que o Screen-App precisa tomar

### 1. Forma da collection

Nenhum campo do contrato foi negociado. O Screen-App precisa decidir, para cada
um: aceita, renomeia ou ignora.

Campos que carregam informação editorial e não deveriam ser descartados sem
discussão: `sources` (atribuição das fontes), `factual_assessment` (cobertura e
contagem de claims), `editorial_gate` (veredito e regras acionadas),
`content_hash`, `assessment_hash`, `gate_hash`.

### 2. Autenticação

O adapter usa `Authorization: Bearer <token>`. **Isso é uma suposição.** Se o
Payload usar cookie de sessão, `API-Key`, ou outro esquema, a mudança é de uma
linha em `PayloadCmsDestination._headers` — mas precisa ser confirmada, não
adivinhada.

### 3. `Idempotency-Key`

O MNScr envia o header em toda criação. **Não há evidência de que o Payload o
honre.** Enquanto isso não for confirmado, a garantia real é:

> **at-least-once com deduplicação best-effort.**

A deduplicação que funciona hoje é local (a tabela `editorial_deliveries` recusa
uma segunda entrega para a mesma chave). Se a resposta se perder depois de o
Payload já ter criado o draft, o MNScr **não sabe** — e por isso registra
`DELIVERY_NEEDS_RECONCILIATION` em vez de recriar às cegas.

Não descreva isso como `exactly-once`. Não é.

### 4. Lookup por `delivery_id` (reconciliação)

`PayloadCmsDestination.find_existing_draft` levanta `NotImplementedError` de
propósito: a sintaxe de query do Payload não foi confirmada, e inventar uma
seria pior que admitir a lacuna.

Para fechar o ciclo de reconciliação, o Screen-App precisa oferecer **uma** destas:

- um campo indexado que guarde o `delivery_id` do MNScr, consultável por query;
- ou um endpoint que devolva o documento criado para uma dada `Idempotency-Key`.

Sem isso, uma entrega ambígua permanece parada para inspeção humana.

### 5. Política de atualização

A MS-5 **nunca atualiza** um draft remoto. Quando o conteúdo muda depois de uma
entrega bem-sucedida, o MNScr registra uma nova entrega versionada com estado
`DELIVERY_REQUIRES_MANUAL_REVIEW` e não toca no documento remoto.

A razão é concreta: sobrescrever um draft que um revisor já pode ter aberto e
editado destruiria trabalho humano. Para permitir atualização, o Screen-App
precisa responder:

- existe `PATCH`/`PUT` na collection?
- é possível saber se um revisor já editou o documento?
- o que deve acontecer se ele já tiver publicado?

### 6. Taxonomia

O MNScr só envia categorias que já existem em `EDITORIAL_CATEGORIES`
(`Notícias`, `Filmes`, `Séries`). Ele **nunca cria taxonomia remota** e bloqueia
a entrega quando o tópico não tem correspondência.

O Screen-App precisa confirmar se essas categorias existem no Payload e como são
referenciadas — string, slug ou id de relacionamento. Se for por id, o mapeamento
precisa ser configurado no MNScr; ele não vai buscar ids remotos.

### 7. Mídia

O MNScr envia **apenas metadados** de imagem: URL de origem, veículo, crédito,
`license_status` e texto alternativo. Ele não baixa, não re-hospeda e não faz
upload, e `remote_media_id` é sempre `null`.

Upload para a collection de mídia do Payload ficou **fora desta fase**: não havia
como validar o contrato. É trabalho posterior, e envolve uma decisão jurídica
sobre direito de uso que o MNScr não pode tomar sozinho.

## Como validar quando o Payload estiver disponível

1. Subir o Payload num ambiente de teste, com a collection criada.
2. Configurar `PAYLOAD_CMS_ENABLED=true` e as demais variáveis num `.env` local.
3. Rodar um ciclo com um evento sintético e conferir:
   - o documento chega como **draft**, nunca publicado;
   - o `remote_draft_id` é persistido em `editorial_deliveries`;
   - reexecutar não cria um segundo documento;
   - um 5xx forçado gera retry e ainda assim um único documento.
4. Só então trocar a frase de encerramento da MS-5 pela versão que afirma
   integração real.

## Fora do escopo da MS-5

Knowledge Graph, embeddings, busca vetorial, aprovação humana, alteração do RSS
Prime e qualquer mudança visual no Screen-App.

**O que saiu desta lista depois que a MS-5 fechou** — registrado aqui porque a
lista original era lida como decisão permanente, e não era: *entity linking* e
*TMDB* entraram em 08–12/08/2026 (`docs/tipos-de-bloco-cinerie.md`), assim como
*upload de mídia* e *publicação automática*.
