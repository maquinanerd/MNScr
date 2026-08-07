# Correções do ciclo de 05/08 12:03

Dois defeitos encontrados no diagnóstico do ciclo de 05/08/2026 12:03, na branch
`claude/ms5-cinerie-autopublish-seo`. Um commit cada, cada um com o teste que prova.

| | commit | assunto |
|---|---|---|
| Defeito 1 | `6a7a8e6` | a guarda de WordPress matava matéria por causa da FONTE |
| Defeito 2 | `887d562` | caminho de arquivo gravado como URL e injetado como link interno |

Base: `72afc24`. 10 arquivos, +495 −37.

---

## Defeito 1 — a guarda de upload do WordPress olhava a forma, não o dono

### O que acontecia

[`app/editorial/models.py`](../app/editorial/models.py) procurava `/wp-content/uploads/`
no `body_html` inteiro. ScreenRant, ComicBook, MovieWeb e Collider **são WordPress** e
servem imagem por esse caminho — a regra bloqueava dado de terceiro por parecer com o
defeito que ela caça.

Evidência em [`logs/app.log`](../logs/app.log), 3 de 4 drafts, todos com um erro só:

```
2026-08-04 10:07:29 - ERROR - pipeline - [DRAFT_INVALID] db_id=7 draft_id=draft-def43035… errors=['WORDPRESS_UPLOAD_URL_PRESENT']
2026-08-05 12:01:34 - ERROR - pipeline - [DRAFT_INVALID] db_id=9 draft_id=draft-d4a1972b… errors=['WORDPRESS_UPLOAD_URL_PRESENT']
2026-08-05 12:03:46 - ERROR - pipeline - [DRAFT_INVALID] db_id=1 draft_id=draft-96eb82ab… errors=['WORDPRESS_UPLOAD_URL_PRESENT']
```

A URL entrava por [`pipeline.py:326-335`](../app/pipeline.py#L326-L335) e
[`html_utils.py:625-647`](../app/html_utils.py#L625-L647), que montam
`<figure><img src=...>` com a URL de imagem da fonte.

### A decisão

A regra **não** foi apagada nem rebaixada a aviso: o WordPress legado do MNScr também
serve por `/wp-content/uploads/`, e o vazamento dele é o que a regra existe para pegar.
O que mudou foi a pergunta que ela faz — de *"esta URL tem forma de WordPress?"* para
*"esta URL é nossa?"*.

Três consequências de projeto:

1. **URL sem host bloqueia.** `/wp-content/uploads/x.jpg` resolve contra o site que
   renderiza o corpo, isto é, contra nós.
2. **Subdomínio conta.** `cdn.nosso-dominio` é nosso; `nosso-dominio.evil.example` não é.
3. **A lista sai do código.** `MNSCR_OWN_CMS_DOMAINS`, **sem valor padrão** — o próprio
   repositório proíbe citar o domínio legado em `app/` e `tests/`
   ([`test_config_and_security.py:101`](../tests/test_config_and_security.py#L101)), e foi
   esse teste que derrubou o default embutido. Lista vazia significa guarda desligada, e o
   startup registra isso.

### As outras duas regras da mesma função

Verificado antes de mudar, conforme pedido. **Nenhuma das duas foi alterada.**

**`_WP_IMAGE_CLASS_PATTERN` (`wp-image-\d+`)** — mesma classe de problema em tese:
WordPress renderiza `class="wp-image-123"` no HTML de front-end, e nada no pipeline tira
`class` ([`hard_filter_forbidden_html`](../app/html_utils.py#L480-L489) só remove `on*` e
`javascript:`). Mas não há caminho vivo: as duas rotas que criam `<img>` montam a tag do
zero, sem `class`, e o writer nunca vê markup da fonte — o audit do artigo das 12:03 tem
`class=` zero vezes. E a string **não carrega host**: dar domínio a ela exigiria varrer o
DOM, achar o elemento com a classe e ler o host do `src` dele.

**`_WP_BLOCK_PATTERN` (`<!-- wp:`)** — é a regra que *menos* tem o problema. Delimitador
de bloco Gutenberg só existe no `post_content` cru; o `do_blocks()` do WordPress consome os
comentários na renderização, então a página pública que o extrator busca não os tem. É
justamente a assinatura de "conteúdo cru do nosso legado vazou".

### Diff

```diff
diff --git a/.env.example b/.env.example
@@ -117,6 +117,21 @@ PAYLOAD_DRAFTS_COLLECTION=editorial-drafts
 PUBLISHER_NAME=Cinerie
 # Domínio usado apenas para sugerir links internos no corpo do draft.
 PUBLISHER_DOMAIN=
+
+# Domínios que são NOSSOS, separados por vírgula. A guarda de markup de CMS usa
+# esta lista para separar vazamento do NOSSO legado de dado da FONTE: uma URL
+# `/wp-content/uploads/` bloqueia quando o host está aqui, e passa quando é de
+# terceiro. ScreenRant, ComicBook e MovieWeb também são WordPress e servem
+# imagem por esse caminho — sem a lista, a guarda mata a matéria pela fonte.
+#
+# Preencha com o domínio do nosso WordPress legado. Ele não tem valor padrão e
+# não aparece no código: o repositório proíbe citar o domínio legado em app/ e
+# tests/ (TestSecurity), e este arquivo é o lugar certo para ele.
+#
+# PUBLISHER_DOMAIN é somado automaticamente. Subdomínios contam.
+# VAZIO DESLIGA A GUARDA: o vazamento do nosso legado passa sem bloqueio. O
+# startup registra um aviso quando isso acontece.
+MNSCR_OWN_CMS_DOMAINS=
 MNSCR_USER_AGENT=MNScr/1.0 (+editorial draft pipeline)
 MNSCR_PROMPT_VERSION=universal_prompt-ms1
 
diff --git a/app/config.py b/app/config.py
@@ -87,6 +87,44 @@ PAYLOAD_CONFIG: Dict[str, Any] = {
 # de publicação.
 PUBLISHER_DOMAIN = (os.getenv('PUBLISHER_DOMAIN') or '').strip().lower()
 
+
+def _domain_list(raw: str) -> List[str]:
+    """Lista de hosts a partir de uma variável separada por vírgula."""
+    hosts = []
+    for chunk in (raw or '').replace(';', ',').split(','):
+        host = chunk.strip().lower().lstrip('.')
+        if host.startswith('www.'):
+            host = host[4:]
+        if host and host not in hosts:
+            hosts.append(host)
+    return hosts
+
+
+# Domínios que são NOSSOS. Servem a uma pergunta só: uma URL encontrada no corpo
+# do draft aponta para o nosso CMS, ou para o site da fonte?
+#
+# A distinção existe porque o legado do MNScr e boa parte das fontes que ele lê
+# (ScreenRant, ComicBook, MovieWeb) rodam o mesmo CMS. Um caminho
+# `/wp-content/uploads/` no corpo é vazamento quando o host é nosso, e é apenas
+# a imagem da fonte quando o host é de terceiro. Sem essa lista a guarda mata a
+# matéria pela FONTE, que foi o que aconteceu no ciclo de 05/08.
+#
+# Sem valor embutido: o domínio legado não volta para o código (é exatamente o
+# que TestSecurity proíbe). Ele é declarado no .env, e a lista vazia é anunciada
+# no startup porque significa guarda desligada.
+#
+# PUBLISHER_DOMAIN entra automaticamente: se ele é o nosso domínio editorial,
+# ele é nosso aqui também.
+OWN_CMS_DOMAINS: List[str] = _domain_list(
+    (os.getenv('MNSCR_OWN_CMS_DOMAINS') or '') + ',' + PUBLISHER_DOMAIN
+)
+
+if not OWN_CMS_DOMAINS:
+    logger.warning(
+        '[CONFIG] MNSCR_OWN_CMS_DOMAINS vazio: a guarda de vazamento de CMS '
+        'proprio nao tem dominio para reconhecer e nao vai bloquear nada.'
+    )
+
 # --- Contrato de entrada (MS-2) ---
 # Contrato canônico do RSS Prime. O feed legado ainda é aceito por um adaptador
 # temporário; quando MNSCR_REQUIRED_INPUT_CONTRACT for fixado em v1, ou quando
diff --git a/app/editorial/models.py b/app/editorial/models.py
@@ -13,6 +13,7 @@ import re
 from dataclasses import dataclass, field
 from datetime import datetime, timezone
 from typing import Any, Optional
+from urllib.parse import urlparse
 
 from .serialization import stable_hash, to_plain
 from .states import (
@@ -35,10 +36,70 @@ OUTPUT_CONTRACT_VERSION = "mnscr-editorial-draft-v0"  # TEMPORARY — awaiting C
 
 PIPELINE_VERSION = "mnscr-ms1"
 
-_WP_UPLOAD_PATTERN = re.compile(r"/wp-content/uploads/", re.IGNORECASE)
 _WP_IMAGE_CLASS_PATTERN = re.compile(r"wp-image-\d+", re.IGNORECASE)
 _WP_BLOCK_PATTERN = re.compile(r"<!--\s*/?wp:", re.IGNORECASE)
 
+#: A URL inteira em volta de um `/wp-content/uploads/`, para que dê para
+#: perguntar de QUEM ela é. Para nos limites que delimitam uma URL dentro de
+#: markup: aspas, espaço, `<`, `>` e parênteses.
+_WP_UPLOAD_URL_PATTERN = re.compile(
+    r"""[^\s"'<>()]*/wp-content/uploads/[^\s"'<>()]*""", re.IGNORECASE
+)
+
+
+def _host_of(url: str) -> Optional[str]:
+    """Host de uma URL de markup, sem `www.`.
+
+    ``None`` significa "sem host": caminho relativo à raiz do site que renderiza
+    o corpo — ou seja, o NOSSO site.
+    """
+    candidate = (url or "").strip()
+    if not candidate:
+        return None
+    # `//host/x` e `host/x` são as duas formas sem esquema que aparecem em
+    # markup; a segunda seria lida como caminho puro se não fosse normalizada.
+    if not candidate.startswith(("/", "http://", "https://")):
+        candidate = "//" + candidate
+    try:
+        host = (urlparse(candidate).hostname or "").lower()
+    except ValueError:
+        return None
+    if not host:
+        return None
+    return host[4:] if host.startswith("www.") else host
+
+
+def _is_own_host(host: Optional[str], own_domains: list[str]) -> bool:
+    """Sem host é nosso. Com host, casa o domínio e seus subdomínios."""
+    if host is None:
+        return True
+    return any(host == d or host.endswith("." + d) for d in own_domains)
+
+
+def own_cms_upload_urls(
+    body_html: str, own_domains: Optional[list[str]] = None
+) -> list[str]:
+    """URLs `/wp-content/uploads/` do NOSSO CMS encontradas no corpo.
+
+    A regra existe para pegar o vazamento do legado do MNScr, que é WordPress.
+    O problema é que ScreenRant, ComicBook e MovieWeb também são WordPress e
+    servem imagem pelo mesmo caminho: procurar só o caminho bloqueia a matéria
+    pela FONTE. Quem decide é o host, não o formato da URL.
+
+    Uma URL sem host (`/wp-content/uploads/x.jpg`) resolve contra o site que
+    renderiza o corpo, isto é, contra nós — e por isso conta como nossa.
+    """
+    if own_domains is None:
+        from app.config import OWN_CMS_DOMAINS
+
+        own_domains = OWN_CMS_DOMAINS
+
+    found: list[str] = []
+    for url in _WP_UPLOAD_URL_PATTERN.findall(body_html or ""):
+        if _is_own_host(_host_of(url), own_domains) and url not in found:
+            found.append(url)
+    return found
+
 
 def _utc_now_iso() -> str:
     return datetime.now(timezone.utc).isoformat()
@@ -333,7 +394,9 @@ def validate_draft(draft: EditorialDraft) -> list[str]:
     flat = str(payload)
     if "wp_post_id" in flat or "featured_media" in flat:
         errors.append("WORDPRESS_IDENTIFIER_PRESENT")
-    if _WP_UPLOAD_PATTERN.search(draft.content.body_html):
+    # Ciente do domínio: bloqueia a URL de upload do NOSSO CMS, deixa passar a
+    # da fonte. Ver `own_cms_upload_urls`.
+    if own_cms_upload_urls(draft.content.body_html):
         errors.append("WORDPRESS_UPLOAD_URL_PRESENT")
     if _WP_IMAGE_CLASS_PATTERN.search(draft.content.body_html):
         errors.append("WORDPRESS_MEDIA_CLASS_PRESENT")
diff --git a/app/editorial_gate/rules.py b/app/editorial_gate/rules.py
@@ -19,8 +19,8 @@ from urllib.parse import urlparse
 from app.editorial.models import (
     _WP_BLOCK_PATTERN,
     _WP_IMAGE_CLASS_PATTERN,
-    _WP_UPLOAD_PATTERN,
     EditorialDraft,
+    own_cms_upload_urls,
 )
 from app.editorial.states import (
     EVIDENCE_CONFLICTING,
@@ -275,8 +275,10 @@ def _find_cms_keys(payload: Any, depth: int = 0) -> List[str]:
 def rule_forbidden_wordpress_markup(draft, policy, context) -> EditorialRuleResult:
     body = draft.content.body_html or ""
     found: List[str] = []
-    if _WP_UPLOAD_PATTERN.search(body):
-        found.append("URL /wp-content/uploads/ no corpo")
+    # Só a URL de upload do NOSSO CMS: a mesma URL vinda da fonte (que também
+    # roda WordPress) é dado de terceiro, não vazamento nosso.
+    for url in own_cms_upload_urls(body):
+        found.append(f"URL /wp-content/uploads/ de dominio proprio: {url}")
     if _WP_IMAGE_CLASS_PATTERN.search(body):
         found.append("classe wp-image-* no corpo")
     if _WP_BLOCK_PATTERN.search(body):
diff --git a/tests/test_editorial_draft_domain.py b/tests/test_editorial_draft_domain.py
@@ -21,7 +21,7 @@ from app.editorial import (
     validate_draft,
     validate_draft_status,
 )
-from app.editorial.models import OUTPUT_CONTRACT_VERSION
+from app.editorial.models import OUTPUT_CONTRACT_VERSION, own_cms_upload_urls
 
 
 def _draft(**overrides):
@@ -149,15 +149,6 @@ class TestValidation:
         errors = validate_draft(draft)
         assert "MISSING_INPUT_HASH" in errors and "MISSING_OUTPUT_HASH" in errors
 
-    def test_wordpress_upload_url_blocks(self):
-        draft = _draft(
-            content=DraftContent(
-                title="Título válido",
-                body_html='<p>x</p><img src="https://site.example/wp-content/uploads/a.jpg">',
-            )
-        )
-        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)
-
     def test_wordpress_media_class_blocks(self):
         draft = _draft(
             content=DraftContent(title="Título válido", body_html='<img class="wp-image-123" src="https://x.example/a.jpg">')
@@ -171,6 +162,105 @@ class TestValidation:
         assert "WORDPRESS_BLOCK_MARKUP_PRESENT" in validate_draft(draft)
 
 
+class TestWordPressUploadGuardIsDomainAware:
+    """A guarda de upload pergunta de QUEM é a URL, não que forma ela tem.
+
+    O ciclo de 05/08 12:03 matou 3 de 4 drafts com WORDPRESS_UPLOAD_URL_PRESENT
+    porque ScreenRant, ComicBook e MovieWeb são WordPress e servem imagem por
+    `/wp-content/uploads/`. A regra bloqueava dado de terceiro por parecer com o
+    defeito que ela caça — o vazamento do nosso próprio legado.
+    """
+
+    OURS = ["cinerie-legado.example"]
+
+    def _draft_with(self, body_html):
+        return _draft(content=DraftContent(title="Título válido", body_html=body_html))
+
+    @pytest.fixture(autouse=True)
+    def _fixed_own_domains(self, monkeypatch):
+        """A lista vem de configuração; o teste não depende do .env da máquina."""
+        import app.config
+
+        monkeypatch.setattr(app.config, "OWN_CMS_DOMAINS", self.OURS)
+
+    @pytest.mark.parametrize(
+        "host",
+        ["screenrant.com", "comicbook.com", "movieweb.com"],
+        ids=["screenrant", "comicbook", "movieweb"],
+    )
+    def test_upload_url_of_external_wordpress_source_does_not_block(self, host):
+        draft = self._draft_with(
+            f'<p>x</p><figure><img src="https://{host}/wp-content/uploads/2026/08/a.jpg" alt=""></figure>'
+        )
+        assert validate_draft(draft) == []
+
+    def test_upload_url_of_our_own_wordpress_still_blocks(self):
+        draft = self._draft_with(
+            '<p>x</p><img src="https://cinerie-legado.example/wp-content/uploads/2026/08/a.jpg">'
+        )
+        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)
+
+    def test_subdomain_of_our_domain_still_blocks(self):
+        draft = self._draft_with(
+            '<p>x</p><img src="https://cdn.cinerie-legado.example/wp-content/uploads/a.jpg">'
+        )
+        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)
+
+    def test_site_relative_upload_url_blocks_because_it_resolves_to_us(self):
+        """Sem host, a URL resolve contra o site que renderiza — nós."""
+        draft = self._draft_with('<p>x</p><img src="/wp-content/uploads/2026/08/a.jpg">')
+        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)
+
+    def test_our_url_blocks_even_ao_lado_de_uma_da_fonte(self):
+        draft = self._draft_with(
+            '<img src="https://screenrant.com/wp-content/uploads/a.jpg">'
+            '<img src="https://cinerie-legado.example/wp-content/uploads/b.jpg">'
+        )
+        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)
+
+
+class TestOwnCmsUploadUrls:
+    """Unidade da decisão de host, sem passar pelo draft inteiro."""
+
+    OURS = ["cinerie-legado.example"]
+
+    @pytest.mark.parametrize(
+        "url",
+        [
+            "https://cinerie-legado.example/wp-content/uploads/a.jpg",
+            "http://www.cinerie-legado.example/wp-content/uploads/a.jpg",
+            "//cinerie-legado.example/wp-content/uploads/a.jpg",
+            "cinerie-legado.example/wp-content/uploads/a.jpg",
+            "/wp-content/uploads/a.jpg",
+        ],
+        ids=["https", "www", "sem-esquema", "host-nu", "relativa"],
+    )
+    def test_our_urls_are_found(self, url):
+        assert own_cms_upload_urls(f'<img src="{url}">', self.OURS) == [url]
+
+    @pytest.mark.parametrize(
+        "url",
+        [
+            "https://screenrant.com/wp-content/uploads/a.jpg",
+            "https://static0.comicbook.com/wp-content/uploads/a.jpg",
+            "screenrant.com/wp-content/uploads/a.jpg",
+        ],
+        ids=["https", "subdominio-de-terceiro", "host-nu"],
+    )
+    def test_third_party_urls_are_not_found(self, url):
+        assert own_cms_upload_urls(f'<img src="{url}">', self.OURS) == []
+
+    def test_a_url_that_only_mentions_our_name_is_not_ours(self):
+        """`cinerie-legado.example.evil.example` não é subdomínio nosso."""
+        body = '<img src="https://cinerie-legado.example.evil.example/wp-content/uploads/a.jpg">'
+        assert own_cms_upload_urls(body, self.OURS) == []
+
+    def test_empty_own_domain_list_blocks_nothing(self):
+        """Sem lista a guarda fica cega — documentado em .env.example."""
+        body = '<img src="https://cinerie-legado.example/wp-content/uploads/a.jpg">'
+        assert own_cms_upload_urls(body, []) == []
+
+
 class TestValueObjects:
     def test_media_candidate_defaults_to_unverified_and_unknown_rights(self):
         media = MediaCandidate(source_url="https://cdn.example/a.jpg")
diff --git a/tests/test_editorial_gate_rules.py b/tests/test_editorial_gate_rules.py
@@ -183,16 +183,25 @@ def test_missing_prompt_version_does_not_trigger_when_present(policy):
     assert not run("GATE_MISSING_PROMPT_VERSION", make_draft(), policy).triggered
 
 
+@pytest.fixture
+def own_domains(monkeypatch):
+    """A lista de domínios nossos vem de configuração, não do .env da máquina."""
+    import app.config
+
+    monkeypatch.setattr(app.config, "OWN_CMS_DOMAINS", ["cinerie-legado.example"])
+    return app.config.OWN_CMS_DOMAINS
+
+
 @pytest.mark.parametrize(
     "body",
     [
         '<p>x</p><!-- wp:image --><figure></figure><!-- /wp:image -->',
         '<p>x</p><img class="wp-image-1234" src="https://a.example/x.jpg">',
-        '<p>x</p><img src="https://a.example/wp-content/uploads/2026/x.jpg">',
+        '<p>x</p><img src="https://cinerie-legado.example/wp-content/uploads/2026/x.jpg">',
     ],
-    ids=["bloco-gutenberg", "classe-wp-image", "url-wp-content"],
+    ids=["bloco-gutenberg", "classe-wp-image", "url-wp-content-nossa"],
 )
-def test_forbidden_wordpress_markup_triggers(policy, body):
+def test_forbidden_wordpress_markup_triggers(policy, own_domains, body):
     draft = make_draft()
     draft.content.body_html = body
     assert run("GATE_FORBIDDEN_WORDPRESS_MARKUP", draft, policy).triggered
@@ -202,6 +211,27 @@ def test_forbidden_wordpress_markup_does_not_trigger_on_clean_html(policy):
     assert not run("GATE_FORBIDDEN_WORDPRESS_MARKUP", make_draft(), policy).triggered
 
 
+@pytest.mark.parametrize(
+    "host",
+    ["screenrant.com", "comicbook.com", "movieweb.com"],
+    ids=["screenrant", "comicbook", "movieweb"],
+)
+def test_upload_url_de_fonte_wordpress_externa_nao_bloqueia(policy, own_domains, host):
+    """A fonte também é WordPress: a imagem dela não é vazamento nosso."""
+    draft = make_draft()
+    draft.content.body_html = f'<p>x</p><img src="https://{host}/wp-content/uploads/2026/08/a.jpg">'
+    result = run("GATE_FORBIDDEN_WORDPRESS_MARKUP", draft, policy)
+    assert not result.triggered
+
+
+def test_evidencia_da_url_de_upload_nomeia_a_url_bloqueada(policy, own_domains):
+    draft = make_draft()
+    draft.content.body_html = '<img src="https://cinerie-legado.example/wp-content/uploads/a.jpg">'
+    result = run("GATE_FORBIDDEN_WORDPRESS_MARKUP", draft, policy)
+    assert result.triggered
+    assert any("cinerie-legado.example/wp-content/uploads/a.jpg" in e for e in result.evidence)
+
+
 @pytest.mark.parametrize(
     "body",
     [
```

---

## Defeito 2 — caminho de arquivo gravado como URL

### O que acontecia

O `link_store` guardava o caminho do artefato em disco no campo `url`:

```
('Star Wars: 14 personagens mais inteligentes da franquia',
 'artifacts\local-drafts\draft-983733e65486f043f9d59f594fb88218.json',
 'movies', 'star wars')
```

Ele voltava pelo `link_map` e o linking interno o injetava no corpo da matéria seguinte
como `<a href>`, com `reason=generic_no_entity_match` — link para matéria sem relação, só
para ter link. No dia em que o destino for o Cinerie, esse valor cai em
`seo.internalLinkSuggestions[].targetPath`, que exige casar `^\/[^\s]*$` dentro de um
objeto `.strict()`: **recusa o pedido inteiro**, não faz strip silencioso.

Eram três rotas de injeção, não uma:

| rota | destino | tinha guarda? |
|---|---|---|
| `ls_get_related` → `ls_format_links` → `_link_block` | prompt do writer | não |
| `select_internal_links` → `internal_link_candidates` | prompt do writer | sim (`_looks_like_url`) |
| `add_internal_links` | corpo, via DOM | não |
| `ls_save_article` | escrita no store | não |

### A decisão

Nenhuma razão para manter o fallback — link falso é pior que link ausente, e concordo com
o diagnóstico. Em `OUTPUT_MODE=local` não existe endereço público para apontar, então
**não há link interno nenhum**: nem sugestão ao writer (as duas rotas), nem inserção no
corpo, nem escrita no `link_store`.

Uma segunda barreira, no próprio `link_store`: ele passa a **recusar na escrita** o que não
for URL pública e a **ignorar na leitura** as linhas já gravadas assim. Isso não depende de
cada chamador lembrar da regra, e limpa o banco atual — que já tem a linha envenenada —
sem migração.

### Diff

```diff
diff --git a/app/link_store.py b/app/link_store.py
@@ -6,6 +6,7 @@ para sugerir links contextuais ao Gemini.
 import logging
 import os
 import sqlite3
+from urllib.parse import urlparse
 
 from .sqlite_utils import connect_sqlite
 
@@ -15,6 +16,22 @@ logger = logging.getLogger(__name__)
 _INITIALIZED = False
 
 
+def _is_public_url(value: str) -> bool:
+    """O campo `url` guarda endereço público, e só isso.
+
+    A checagem existe porque o pipeline já gravou aqui
+    `artifacts\\local-drafts\\draft-....json` — o caminho do artefato em disco,
+    que o linking interno depois injetou no corpo como <a href>. Um caminho de
+    arquivo não é endereço de nada; a barreira fica no armazenamento para não
+    depender de cada chamador lembrar disso.
+    """
+    try:
+        parsed = urlparse(str(value or "").strip())
+    except ValueError:
+        return False
+    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
+
+
 def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
     row = conn.execute(
         "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
@@ -92,7 +109,17 @@ def _conn() -> sqlite3.Connection:
 
 
 def save_article(title: str, url: str, category: str = "", entity: str = "") -> None:
-    """Salva artigo publicado. Mantém apenas os 200 mais recentes."""
+    """Salva artigo publicado. Mantém apenas os 200 mais recentes.
+
+    Recusa o que não for endereço público: o link_store existe para virar
+    <a href> no corpo da próxima matéria.
+    """
+    if not _is_public_url(url):
+        logger.warning(
+            "[LINKS] recusado no link_store: %r nao e URL publica (title=%r)",
+            url, (title or "")[:60],
+        )
+        return
     try:
         with _conn() as c:
             c.execute(
@@ -120,7 +147,12 @@ def get_related(entity: str = "", category: str = "", limit: int = 3) -> list:
                     "SELECT title, url FROM link_store WHERE entity = ? ORDER BY published DESC LIMIT ?",
                     (entity, limit),
                 ).fetchall()
-                results = [{"title": r["title"], "url": r["url"]} for r in rows]
+                # Linhas gravadas antes da barreira de escrita ainda podem
+                # carregar caminho de arquivo; elas não voltam para o prompt.
+                results = [
+                    {"title": r["title"], "url": r["url"]}
+                    for r in rows if _is_public_url(r["url"])
+                ]
 
             if len(results) < limit and category:
                 needed = limit - len(results)
@@ -130,6 +162,8 @@ def get_related(entity: str = "", category: str = "", limit: int = 3) -> list:
                     (category, needed * 2),
                 ).fetchall()
                 for r in rows:
+                    if not _is_public_url(r["url"]):
+                        continue
                     if r["url"] not in existing_urls:
                         results.append({"title": r["title"], "url": r["url"]})
                     if len(results) >= limit:
@@ -155,6 +189,8 @@ def get_link_map() -> dict:
         for row in rows:
             title = row["title"]
             url = row["url"]
+            if not _is_public_url(url):
+                continue
             entity = row["entity"]
             kws = []
             # Entity curta (ex: "marvel", "one piece") → ótima para match
diff --git a/app/pipeline.py b/app/pipeline.py
@@ -89,7 +89,7 @@ from .policy_engine import (
 )
 from .seo_title_optimizer import optimize_title
 from .store import Database
-from .submitters import build_submitter
+from .submitters import OUTPUT_MODE_LOCAL, build_submitter
 from .superfeed_policy import check_superfeed_policy
 from .task_queue import ArticleQueue
 from .title_validator import TitleValidator
@@ -119,6 +119,21 @@ CLAIM_STALE_TIMEOUT_S = int(os.getenv('CLAIM_STALE_TIMEOUT_S', ARTICLE_WATCHDOG_
 # Versao do prompt canonico, registrada na proveniencia de cada draft.
 PROMPT_VERSION = os.getenv('MNSCR_PROMPT_VERSION', 'universal_prompt-ms1')
 
+# Link interno so existe quando ha um site para onde apontar.
+#
+# Em OUTPUT_MODE=local o draft nao vira pagina: o unico "endereco" que ele tem e
+# o caminho do artefato em disco (artifacts/local-drafts/draft-....json). Esse
+# valor nao e URL de nada. Gravado no link_store, ele volta como <a href> para um
+# arquivo no corpo da materia seguinte; e no dia em que o destino for o Cinerie
+# ele cai em seo.internalLinkSuggestions[].targetPath, que exige casar
+# ^\/[^\s]*$ dentro de um objeto .strict() — recusa o pedido inteiro, sem strip
+# silencioso.
+#
+# Entao em modo local nao ha link interno nenhum: nem sugestao ao writer, nem
+# insercao no corpo, nem escrita no link_store. Link falso e pior que link
+# ausente.
+INTERNAL_LINKING_ENABLED = OUTPUT_MODE != OUTPUT_MODE_LOCAL
+
 CLEANER_FUNCTIONS = {
     'globo.com': clean_html_for_globo_esporte,
 }
@@ -1108,7 +1123,7 @@ def process_batch(articles: List[Dict[str, Any]], link_map: Dict[str, Any]):
                         topic=art.get('cluster_item', {}).get('topic', art.get('category', '')),
                         k=6,
                         current_url=art.get('url', ''),
-                    )
+                    ) if INTERNAL_LINKING_ENABLED else []
                     ai_payload['internal_link_candidates'] = link_candidates
                     logger.info(
                         "[LINKS] writer candidates=%s db_id=%s",
@@ -1170,7 +1185,7 @@ def process_batch(articles: List[Dict[str, Any]], link_map: Dict[str, Any]):
                     entity=_pre_event.get("entity", ""),
                     category=art['category'],
                     limit=3,
-                )
+                ) if INTERNAL_LINKING_ENABLED else []
                 _link_block = ls_format_links(_related)
                 link_candidates = select_internal_links(
                     link_map=link_map,
@@ -1180,7 +1195,7 @@ def process_batch(articles: List[Dict[str, Any]], link_map: Dict[str, Any]):
                     topic=art.get('feed_config', {}).get('topic') or art.get('category', ''),
                     k=6,
                     current_url=art.get('url', ''),
-                )
+                ) if INTERNAL_LINKING_ENABLED else []
                 logger.info(
                     "[LINKS] writer candidates=%s db_id=%s",
                     len(link_candidates), art.get('db_id', '?'),
@@ -1406,7 +1421,7 @@ def process_batch(articles: List[Dict[str, Any]], link_map: Dict[str, Any]):
                             {'nome': name} for name in category_suggestions
                         ]
 
-                        if link_map:
+                        if INTERNAL_LINKING_ENABLED and link_map:
                             content_html = add_internal_links(
                                 html_content=content_html, link_map_data=link_map,
                                 current_post_categories=[])
@@ -1624,18 +1639,30 @@ def process_batch(articles: List[Dict[str, Any]], link_map: Dict[str, Any]):
                         _publish_to_cinerie(draft, art_data)
 
                         # Link store: alimenta sugestoes de links internos futuros.
-                        from .cluster_engine import score_event
-                        event = score_event({
-                            "title":   title,
-                            "content": content_html,
-                            "tags":    rewritten_data.get("tags_sugeridas", []),
-                        })
-                        ls_save_article(
-                            title=title,
-                            url=result.artifact_path or draft.draft_id,
-                            category=art_data['category'],
-                            entity=event.get("entity", ""),
-                        )
+                        #
+                        # Em modo local nao ha o que alimentar: `artifact_path` e
+                        # um caminho de arquivo e `draft_id` nao e endereco de
+                        # nada. Gravar qualquer um dos dois transformaria a
+                        # proxima materia em portadora de um link falso.
+                        if INTERNAL_LINKING_ENABLED:
+                            from .cluster_engine import score_event
+                            event = score_event({
+                                "title":   title,
+                                "content": content_html,
+                                "tags":    rewritten_data.get("tags_sugeridas", []),
+                            })
+                            ls_save_article(
+                                title=title,
+                                url=result.artifact_path or draft.draft_id,
+                                category=art_data['category'],
+                                entity=event.get("entity", ""),
+                            )
+                        else:
+                            logger.info(
+                                "[LINKS] link_store ignorado draft_id=%s motivo=output_mode=%s "
+                                "sem endereco publico para o draft",
+                                draft.draft_id, OUTPUT_MODE,
+                            )
 
                         if art_data.get('is_cluster'):
                             event_key = (art_data.get('cluster_item') or {}).get('event_key', '')
diff --git a/tests/test_link_store_url_guard.py b/tests/test_link_store_url_guard.py
new file mode 100644
--- /dev/null
+++ b/tests/test_link_store_url_guard.py
@@ -0,0 +1,76 @@
+"""O campo `url` do link_store guarda endereço público, e só isso.
+
+O ciclo de 05/08 gravou ali `artifacts\\local-drafts\\draft-....json` — o
+caminho do artefato em disco. Ele voltou pelo link_map e o linking interno o
+injetou no corpo da matéria seguinte como <a href>. A barreira fica no
+armazenamento para não depender de cada chamador lembrar disso.
+"""
+
+from __future__ import annotations
+
+import pytest
+
+from app import link_store
+
+FILE_PATH = "artifacts\\local-drafts\\draft-983733e65486f043f9d59f594fb88218.json"
+REAL_URL = "https://cinerie.example/star-wars-personagens-inteligentes/"
+
+
+@pytest.fixture(autouse=True)
+def store_in_tmp(tmp_path, monkeypatch):
+    """Um banco por teste; o link_store guarda o caminho num global de módulo."""
+    monkeypatch.setattr(link_store, "_DB", str(tmp_path / "app.db"))
+    monkeypatch.setattr(link_store, "_LEGACY_DB", str(tmp_path / "app.db"))
+    monkeypatch.setattr(link_store, "_INITIALIZED", False)
+
+
+def _rows() -> list[tuple[str, str]]:
+    with link_store._conn() as c:
+        return [(r["title"], r["url"]) for r in c.execute("SELECT title, url FROM link_store")]
+
+
+class TestWriteRefusesWhatIsNotAnAddress:
+    @pytest.mark.parametrize(
+        "value",
+        [
+            FILE_PATH,
+            "artifacts/local-drafts/draft-abc.json",
+            "draft-983733e65486f043f9d59f594fb88218",
+            "/wp-content/uploads/a.jpg",
+            "",
+            "   ",
+        ],
+        ids=["caminho-windows", "caminho-posix", "draft-id", "relativa", "vazia", "espacos"],
+    )
+    def test_non_url_is_not_stored(self, value):
+        link_store.save_article(title="Star Wars", url=value, category="movies")
+        assert _rows() == []
+
+    def test_a_real_url_is_stored(self):
+        link_store.save_article(title="Star Wars", url=REAL_URL, category="movies")
+        assert _rows() == [("Star Wars", REAL_URL)]
+
+
+class TestReadIgnoresRowsWrittenBeforeTheGuard:
+    """O banco em produção já tem a linha envenenada; ela não volta ao prompt."""
+
+    def _poison_directly(self):
+        with link_store._conn() as c:
+            c.execute(
+                "INSERT INTO link_store (title, url, category, entity) VALUES (?, ?, ?, ?)",
+                ("Star Wars: 14 personagens", FILE_PATH, "movies", "star wars"),
+            )
+
+    def test_get_link_map_skips_it(self):
+        self._poison_directly()
+        assert link_store.get_link_map() == {"posts": []}
+
+    def test_get_related_skips_it(self):
+        self._poison_directly()
+        assert link_store.get_related(entity="star wars", category="movies") == []
+
+    def test_a_clean_row_beside_it_still_comes_back(self):
+        self._poison_directly()
+        link_store.save_article(title="Marvel", url=REAL_URL, category="movies", entity="marvel")
+        links = [p["link"] for p in link_store.get_link_map()["posts"]]
+        assert links == [REAL_URL]
diff --git a/tests/test_pipeline_draft_cycle.py b/tests/test_pipeline_draft_cycle.py
@@ -7,6 +7,7 @@ article produces exactly one draft artifact and touches nothing else.
 from __future__ import annotations
 
 import json
+import re
 import socket
 from pathlib import Path
 
@@ -220,3 +221,83 @@ class TestPipelineProducesDrafts:
         count = db._get_cursor().execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
         db.close()
         assert count == 0, "MNScr nao pode registrar publicacoes"
+
+
+#: O link_map envenenado que o ciclo de 05/08 produziu: o link_store guardou o
+#: caminho do artefato no campo `url`, e o linking interno o injetou no corpo
+#: como <a href> com reason=generic_no_entity_match — link para materia sem
+#: relacao, so para ter link.
+POISONED_LINK_MAP = {
+    "posts": [
+        {
+            "link": "artifacts\\local-drafts\\draft-983733e65486f043f9d59f594fb88218.json",
+            "keywords": ["Star Wars", "Star Wars: 14 personagens mais inteligentes"],
+            "categories": [],
+        }
+    ]
+}
+
+
+class TestLocalModeInsertsNoInternalLink:
+    """Em OUTPUT_MODE=local nao existe endereco publico para apontar.
+
+    O draft nao vira pagina; o unico "endereco" que ele tem e o caminho do
+    artefato em disco. Injetado no corpo vira <a href> para um arquivo, e no
+    Cinerie cairia em seo.internalLinkSuggestions[].targetPath, que exige casar
+    ^\\/[^\\s]*$ dentro de um objeto .strict(): recusa o pedido inteiro.
+    """
+
+    @pytest.fixture
+    def link_store_calls(self, sandbox, monkeypatch):
+        calls: list[dict] = []
+        monkeypatch.setattr(pipeline, "ls_save_article", lambda **kwargs: calls.append(kwargs))
+        monkeypatch.setattr(pipeline, "ls_get_link_map", lambda: POISONED_LINK_MAP)
+        monkeypatch.setattr(pipeline, "ls_get_related", lambda **kwargs: [
+            {"title": "Star Wars", "url": POISONED_LINK_MAP["posts"][0]["link"]}
+        ])
+        return calls
+
+    def _body_of_the_draft(self, sandbox):
+        payload = json.loads(
+            next(Path(sandbox["drafts_dir"]).glob("*.json")).read_text(encoding="utf-8")
+        )
+        return payload["content"]["body_html"]
+
+    def _run_with_poisoned_link_map(self, sandbox):
+        db = Database(str(sandbox["db_path"]))
+        db.initialize()
+        items = pipeline.FeedReader(user_agent="test").read_feeds(
+            pipeline.RSS_FEEDS["screenrant_movie_news"], "screenrant_movie_news"
+        )
+        article = db.filter_new_articles("screenrant_movie_news", items, limit=1)[0]
+        article["source_id"] = "screenrant_movie_news"
+        db.close()
+
+        pipeline.article_queue.push_many([article])
+        claimed = pipeline.article_queue.pop_claimed("test-worker")
+        pipeline.process_batch([claimed], link_map=POISONED_LINK_MAP)
+        return claimed
+
+    def test_the_local_mode_disables_internal_linking(self):
+        assert pipeline.INTERNAL_LINKING_ENABLED is False
+
+    def test_no_file_path_is_injected_into_the_body(self, sandbox, link_store_calls):
+        self._run_with_poisoned_link_map(sandbox)
+        body = self._body_of_the_draft(sandbox)
+        assert "local-drafts" not in body
+        assert ".json" not in body
+
+    def test_no_fallback_link_is_appended(self, sandbox, link_store_calls):
+        """O fallback existia so para ter link; link falso e pior que ausente."""
+        self._run_with_poisoned_link_map(sandbox)
+        assert "Leia tambem" not in self._body_of_the_draft(sandbox)
+
+    def test_the_only_anchors_left_are_the_source_credit(self, sandbox, link_store_calls):
+        self._run_with_poisoned_link_map(sandbox)
+        body = self._body_of_the_draft(sandbox)
+        for href in re.findall(r'href="([^"]+)"', body):
+            assert href.startswith("https://deadline.example"), f"link nao-fonte no corpo: {href}"
+
+    def test_nothing_is_written_to_the_link_store(self, sandbox, link_store_calls):
+        self._run_with_poisoned_link_map(sandbox)
+        assert link_store_calls == []
```

---

## Verificação

### Os testes falham sem o conserto

Neutralizei as duas linhas centrais (`INTERNAL_LINKING_ENABLED = True` e o retorno de
`_is_public_url`) e rodei os testes novos: **os 5 do pipeline e os 12 do link_store
quebraram**. Depois restaurei.

```
FAILED tests/test_pipeline_draft_cycle.py::TestLocalModeInsertsNoInternalLink::test_the_local_mode_disables_internal_linking
FAILED tests/test_pipeline_draft_cycle.py::TestLocalModeInsertsNoInternalLink::test_no_file_path_is_injected_into_the_body
FAILED tests/test_pipeline_draft_cycle.py::TestLocalModeInsertsNoInternalLink::test_no_fallback_link_is_appended
FAILED tests/test_pipeline_draft_cycle.py::TestLocalModeInsertsNoInternalLink::test_the_only_anchors_left_are_the_source_credit
FAILED tests/test_pipeline_draft_cycle.py::TestLocalModeInsertsNoInternalLink::test_nothing_is_written_to_the_link_store
```

### Suíte e lint

```
3056 passed, 79 warnings in 88.19s     # sem --ignore
All checks passed!                     # ruff
```

### Ciclo real, 05/08 15:30

**`--once` sozinho não produz draft.** Ele só faz a *ingestão*; quem gera draft é a thread
worker, que é daemon e morre quando o processo sai. Com 15 itens já na fila, o
`[CYCLE_GUARD]` ignorou o ciclo e o processo terminou em 0 s sem tocar em artigo nenhum.
O ciclo abaixo foi feito rodando `python -m app.main`, parado assim que o primeiro draft
passou pelo gate.

Draft `draft-5f11f6b78a25c947b3d00d125d04dade` — *"Spider-Man: Brand New Day quebra
recorde de bilheteria no MCU"*, fontes Variety + Collider.

**Defeito 1 confirmado em dado real.** `blocking_errors` do `validate_draft` = `[]`, com o
corpo carregando exatamente o padrão que matava:

```
https://static0.colliderimages.com/wordpress/wp-content/uploads/2026/08/img_2489-1.jpg
https://static1.colliderimages.com/wordpress/wp-content/uploads/audio-reader/…/….mp3
```

Collider é mais um WordPress na lista — com a regra antiga esse seria o quarto óbito.

**Defeito 2 confirmado em dado real.** Os únicos `href` do corpo são os dois créditos de
fonte (variety.com, collider.com). Zero "Leia tambem", zero caminho de arquivo. O log
registra:

```
[LINKS] link_map atualizado: 0 do DB + 0 do JSON = 0 total       # linha envenenada filtrada na leitura
[QA] … | links=0/0 | …                                          # nenhum link interno inserido
[LINKS] link_store ignorado draft_id=draft-5f11f6b7… motivo=output_mode=local sem endereco publico para o draft
```

**Veredito do gate: `GATE_BLOCKED` — 1 bloqueante, 6 warnings.** Não é a guarda de
WordPress:

| severidade | regra | mensagem |
|---|---|---|
| **BLOCKING** | `GATE_CRITICAL_FACT_CONFLICT` | 6 conflitos críticos entre fontes — todos `NUMBER_MISMATCH` de bilheteria (USD 360.000.000 vs 357.000.000 vs 260.000.000) |
| WARNING | `GATE_LEGACY_INPUT_CONTRACT` | draft originado do feed legado |
| WARNING | `GATE_EVIDENCE_MISSING` | 12 afirmações materiais sem evidência associada |
| WARNING | `GATE_EVIDENCE_CONFLICT` | 6 conflitos factuais, 4 afirmações contraditadas |
| WARNING | `GATE_UNVERIFIED_MEDIA` | 5 candidatos de mídia sem licença verificada |
| WARNING | `GATE_FACTUAL_COVERAGE_LOW` | cobertura factual 0.0, abaixo do mínimo 0.75 |
| WARNING | `GATE_UNVERIFIED_CLAIMS_PRESENT` | 5 afirmações sem dados suficientes para concluir |
| INFO | `GATE_MULTI_SOURCE` | 2 domínios distintos sustentam o draft |
| INFO | `GATE_LOCAL_OUTPUT_ONLY` | destino operacional: local, nenhuma publicação |

O bloqueio é o gate fazendo o trabalho dele: as fontes discordam do número de bilheteria e
ninguém escolhe versão automaticamente.

---

## Pendências

**1. `MNSCR_OWN_CMS_DOMAINS` não está no `.env` — sem ela a guarda não bloqueia nada.**
O ciclo acima rodou com a variável setada só para aquele processo; o `.env` continua
intocado. O startup avisa:

```
[CONFIG] MNSCR_OWN_CMS_DOMAINS vazio: a guarda de vazamento de CMS proprio nao tem dominio para reconhecer e nao vai bloquear nada.
```

O valor é o host de `WORDPRESS_SITE_URL`, que já está no `.env`. **Decisão pendente:
adicionar a linha ao `.env`.**

**2. `GATE_FACTUAL_COVERAGE_LOW` com cobertura 0.0 enquanto o `factual_builder` extraiu 24
evidências** parece defeito de ligação entre claims e evidências, não sinal editorial. Fora
do escopo dos dois defeitos; **não confirmado, não investigado.**

**3. A linha envenenada continua em `data/app.db`.** É intencional: o conserto filtra na
leitura em vez de migrar. Ela nunca mais volta ao prompt nem ao corpo.

---

Nada foi mesclado e nada foi enviado (`push`). Branch `claude/ms5-cinerie-autopublish-seo`,
2 commits à frente de `72afc24`.
