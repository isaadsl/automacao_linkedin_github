from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

PESSOAS_POR_LOTE = 50
PAUSA_SEGUNDOS = 10


_SESSION_GUARD = None


class StopRun(RuntimeError):
    pass


class SkipProfile(RuntimeError):
    pass


class PlatformRestriction(StopRun):
    pass


class SessionEnded(PlatformRestriction):
    pass


def session_url_message(url):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    path = parsed.path.lower().rstrip('/')
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        if path.startswith(('/authwall', '/login', '/uas/login', '/signup', '/start/join')):
            return 'LinkedIn desconectado: a página exige entrar na conta novamente.'
    elif host in {'github.com', 'www.github.com'} and path in {'/login', '/session'}:
        return 'GitHub desconectado: a página exige entrar na conta novamente.'
    return None


def check_session(page):
    message = session_url_message(page.url)
    if message:
        raise SessionEnded(message)
    host = (urlparse(page.url).hostname or '').lower()
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        markers = (
            'input[name="session_key"], input[name="session_password"], '
            'form[action*="/uas/login"], '
            'header a[href*="/login"], [role="banner"] a[href*="/login"], '
            'nav a[href*="/login"]'
        )
        if visible(page.locator(markers)):
            raise SessionEnded('LinkedIn desconectado: formulário ou navegação de login visível.')
        if visible(page.get_by_role('heading', name=re.compile(
            r'^(Cadastre-se no LinkedIn|Join LinkedIn|Entrar no LinkedIn|Sign in to LinkedIn)$', re.I))):
            raise SessionEnded('LinkedIn desconectado: tela de entrada ou cadastro visível.')
    elif host in {'github.com', 'www.github.com'}:
        if visible(page.locator('header a[href^="/login"], input[name="login"], input[name="password"]')):
            raise SessionEnded('GitHub desconectado: tela ou navegação de login visível.')


class SessionGuard:
    def __init__(self, pages):
        self.pages = pages
        self.message = None
        for page in pages:
            page.on('framenavigated', lambda frame, owner=page: self.on_navigation(owner, frame))

    def on_navigation(self, owner, frame):
        if frame == owner.main_frame:
            message = session_url_message(frame.url)
            if message:
                self.message = message

    def check(self):
        if self.message:
            raise SessionEnded(self.message)
        for page in self.pages:
            if not page.is_closed():
                try:
                    check_session(page)
                except SessionEnded as error:
                    self.message = str(error)
                    raise
                except Exception:
                    message = session_url_message(page.url)
                    if message:
                        self.message = message
                        raise SessionEnded(message)
        if self.message:
            raise SessionEnded(self.message)


def guard_sessions():
    if _SESSION_GUARD is not None:
        _SESSION_GUARD.check()


def pause_with_session_checks(seconds):
    deadline = time.monotonic() + seconds
    while True:
        guard_sessions()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))


def normalize_url(value, platform):
    value = str(value or '').strip()
    if not value:
        return ''
    if '://' not in value:
        identifier = value.removeprefix('@') if platform == 'github' else value
        if platform == 'linkedin':
            decoded = unquote(identifier)
            valid_identifier = bool(re.fullmatch(r'[\w-]+', decoded))
            prefix = 'https://www.linkedin.com/in/'
        else:
            valid_identifier = bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', identifier))
            prefix = 'https://github.com/'
        if not valid_identifier:
            raise ValueError(f'Identificador ambíguo ou inválido ({platform}): {value}')
        value = prefix + identifier
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower()
    path = parsed.path.rstrip('/')
    valid = parsed.scheme == 'https' and not parsed.username and not parsed.password
    if platform == 'linkedin':
        valid = valid and host in {'linkedin.com', 'www.linkedin.com'}
        valid = valid and bool(re.fullmatch(r'/in/[^/]+', path))
        host = 'www.linkedin.com'
    else:
        valid = valid and host in {'github.com', 'www.github.com'}
        valid = valid and bool(re.fullmatch(r'/[A-Za-z0-9][A-Za-z0-9-]{0,38}', path))
        host = 'github.com'
    if not valid or parsed.port not in (None, 443):
        raise ValueError(f'URL de perfil inválida ({platform}): {value}')
    return f'https://{host}{quote(unquote(path), safe="/-_")}'


def read_people(path, issues=None):
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(stream, dialect=dialect))
    elif path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            values = iter(book.active.values)
            headers = next(values, ())
            rows = [dict(zip(headers, row)) for row in values]
        finally:
            book.close()
    else:
        raise ValueError('Use uma tabela .csv ou .xlsx.')
    people = []
    seen = set()
    for number, row in enumerate(rows, 2):
        row = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
        if not {'linkedin', 'github'} & row.keys():
            raise ValueError('A tabela precisa de uma coluna linkedin e/ou github.')
        person = {'nome': str(row.get('nome') or f'Linha {number}')}
        for platform in ('linkedin', 'github'):
            raw_username = row.get(platform)
            if raw_username is None or not str(raw_username).strip():
                print(f'Linha {number}: {platform} sem usuário preenchido; pulando essa plataforma.')
                continue
            else:
                raw_username = str(raw_username).strip()
            try:
                url = normalize_url(raw_username, platform)
            except ValueError as error:
                if issues is None:
                    raise
                issues.append({'linha': number, 'coluna': platform,
                               'valor': str(row.get(platform)), 'motivo': str(error)})
                continue
            if url and url.lower() not in seen:
                person[platform] = url
                seen.add(url.lower())
        if len(person) > 1:
            people.append(person)
    return people


def save_status(path, state, url, status):
    state[url] = {'status': status, 'atualizado': datetime.now(timezone.utc).isoformat()}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def visible(locator):
    return [item for item in locator.all() if item.is_visible()]


FIRST_DEGREE = re.compile(
    r'^\s*(?:[·•]\s*)?(?:1st(?:\s+degree)?|1[º°](?:\s+grau)?|1\.[º°](?:\s+grau)?|'
    r'conexão de 1[º°] grau|1st degree connection)\s*$', re.I
)


def already_connected(card):
    return bool(visible(card.get_by_text(FIRST_DEGREE))
                or visible(card.get_by_label(FIRST_DEGREE)))


def linkedin_card(page, url):
    main = page.get_by_role('main')
    only_one(main, 'Área principal do LinkedIn')
    primary = main.get_by_role('region', name=re.compile(r'^(Conteúdo principal|Main content)$', re.I))
    try:
        only_one(primary, 'Área do perfil', timeout_ms=5000)
    except SessionEnded:
        raise
    except Exception:
        pass  
    has_primary = len(visible(primary)) == 1
    scope = primary if has_primary else main
    path = urlparse(url).path.rstrip('/')
    hrefs = [url, url + '/', path, path + '/']
    profile_links = scope.locator(', '.join(f'a[href="{href}"]' for href in hrefs))
    if has_primary:
        titles = scope.locator('h1, h2').first
    else:
        titles = scope.locator('h1').or_(profile_links.locator('h2'))
    try:
        heading = only_one(titles, 'Nome do perfil na área principal do LinkedIn')
    except StopRun as error:
        check_restrictions(page)
        raise SkipProfile(str(error)) from error
    section = heading.locator('xpath=ancestor::section[1]')
    if section.count() == 1:
        return section
    card = heading.locator(
        'xpath=ancestor::div[.//a[contains(@href,"/messaging/compose") '
        'or contains(@href,"/preload/custom-invite/")]][1]'
    )
    if card.count() != 1:
        raise SkipProfile('Cartão principal não reconhecido; perfil pulado sem clicar.')
    return card


def profile_controls(card, name):
    return card.get_by_role('button', name=name).or_(card.get_by_role('link', name=name))


def only_one(locator, label='elemento esperado', timeout_ms=20000):
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        guard_sessions()
        matches = visible(locator)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise StopRun(f'{label}: encontrei {len(matches)} elementos visíveis; ação não realizada.')
        if time.monotonic() >= deadline:
            raise StopRun(f'{label}: não apareceu em até {timeout_ms / 1000:g} segundos.')
        time.sleep(0.2)


def save_diagnostics(page, reason):
    folder = Path(__file__).resolve().parent / 'diagnosticos' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True, exist_ok=True)
    report = {'url': page.url, 'motivo': str(reason)}
    try:
        page.screenshot(path=str(folder / 'pagina.png'), full_page=True, timeout=15000)
    except Exception as error:
        report['erro_imagem'] = str(error)
    try:
        (folder / 'elementos.txt').write_text(page.locator('body').aria_snapshot(timeout=15000), encoding='utf-8')
    except Exception as error:
        report['erro_elementos'] = str(error)
    (folder / 'relatorio.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Diagnóstico salvo em: {folder}')
    return folder


def check_restrictions(page):
    check_session(page)
    url = page.url.lower()
    patterns = (
        'security verification', 'verificação de segurança', 'verify you are human',
        'confirme que você é humano', 'unusual activity', 'atividade incomum',
        'temporarily restricted', 'temporariamente restrita',
        'weekly invitation limit', 'limite semanal de convites',
        'you have exceeded a secondary rate limit', 'too many requests',
    )
    if any(part in url for part in ('/checkpoint', '/challenge', '/login', '/signin', '/authwall')):
        raise PlatformRestriction('Login ou verificação exigida. Execução interrompida.')
    text = page.locator('body').inner_text(timeout=15000).lower()
    if any(term in text for term in patterns) or visible(page.locator('iframe[src*="captcha"]')):
        raise PlatformRestriction('Possível restrição ou verificação. Execução interrompida.')


def act(page, platform, url, before_click):
    guard_sessions()
    response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
    guard_sessions()
    check_session(page)
    if response and response.status in {401, 403, 429}:
        raise PlatformRestriction(f'HTTP {response.status}: acesso ou frequência restritos.')
    if response and response.status >= 400:
        raise SkipProfile(f'A página respondeu com HTTP {response.status}.')
    check_restrictions(page)
    if normalize_url(page.url, platform).lower() != url.lower():
        raise SkipProfile('O endereço mudou para outro perfil. Confira manualmente.')

    if platform == 'github':
        username = url.rsplit('/', 1)[-1]
        main = page.get_by_role('main')
        follow = main.get_by_role('button', name=re.compile(
            rf'^(?:Follow|Seguir)(?:\s+{re.escape(username)})?$', re.I))
        unfollow = main.get_by_role('button', name=re.compile(
            rf'^(?:Unfollow|Deixar de seguir)(?:\s+{re.escape(username)})?$', re.I))
        only_one(follow.or_(unfollow), 'Controle Seguir ou Deixar de seguir do perfil GitHub')
        if visible(unfollow):
            return 'ja_seguindo'
        else:
            button = only_one(follow, 'Botão Seguir do GitHub')
            before_click()
            button.click()
            only_one(unfollow, 'Confirmação de que o perfil foi seguido', timeout_ms=15000)
            check_restrictions(page)
            return 'seguido'

    card = linkedin_card(page, url)
    if already_connected(card):
        return 'ja_conectado'
    pending = profile_controls(card, re.compile(r'^(Pending|Pendente|Convite pendente)(\b.*)?$', re.I))
    message = profile_controls(card, re.compile(r'^(Message|Enviar mensagem|Mensagem)$', re.I))
    if visible(pending):
        return 'convite_pendente'
    connect_pattern = re.compile(r'^(Connect|Conectar|Conectar-se)$|^Invite .+ to connect$|^Convidar .+ para se conectar$', re.I)
    connect = profile_controls(card, connect_pattern)
    if not visible(connect):
        more = card.get_by_role('button', name=re.compile(r'^(More|Mais)( actions| ações)?$', re.I))
        if len(visible(more)) != 1:
            if visible(message):
                return 'revisar_conexao' 
            raise StopRun('Botão Conectar não localizado no perfil.')
        only_one(more, 'Botão Mais do perfil').click()
        guard_sessions()
        connect = page.get_by_role('menuitem', name=connect_pattern).or_(profile_controls(card, connect_pattern))
    button = only_one(connect, 'Botão Conectar do perfil visitado')
    before_click()
    button.click()
    dialogs = page.get_by_role('dialog')
    try:
        only_one(dialogs, 'Janela de confirmação', timeout_ms=5000)
    except SessionEnded:
        raise
    except Exception:
        if visible(pending):
            return 'convite_enviado'
        raise StopRun('Confirmação do convite não reconhecida; revise antes de repetir.')
    dialog = only_one(dialogs, 'Janela de confirmação do convite')
    send = dialog.get_by_role('button', name=re.compile(r'^(Send without a note|Enviar sem nota|Enviar sem uma nota|Enviar sem mensagem|Send now|Send|Enviar|Enviar agora)$', re.I))
    send_button = only_one(send, 'Botão de enviar convite sem nota')
    guard_sessions()
    send_button.click()
    only_one(pending, 'Confirmação de convite pendente', timeout_ms=15000)
    check_restrictions(page)
    return 'convite_enviado'


def process_profile(page, platform, url, state, state_path):
    """Uma falha individual não impede o processamento dos próximos perfis."""
    clicked = False

    def before_click():
        nonlocal clicked
        guard_sessions()
        save_status(state_path, state, url, 'revisar_acao_iniciada')
        clicked = True

    try:
        result = act(page, platform, url, before_click)
        guard_sessions()
        save_status(state_path, state, url, result)
        if result in {'ja_seguindo', 'ja_conectado', 'convite_pendente', 'revisar_conexao'}:
            print(f'{result}: pulando para o próximo perfil.')
        else:
            print(result)
    except SessionEnded:
        save_status(state_path, state, url, 'revisar_acao_iniciada' if clicked else 'sessao_encerrada')
        raise
    except Exception as error:
        restriction = error if isinstance(error, PlatformRestriction) else None
        if restriction is None:
            try:
                check_restrictions(page)
            except PlatformRestriction as detected:
                restriction = detected
            except Exception:
                pass
        status = 'revisar_acao_iniciada' if clicked else (
            'revisar_layout' if isinstance(error, SkipProfile) else 'revisar_erro')
        save_status(state_path, state, url, status)
        if isinstance(restriction, SessionEnded):
            raise restriction
        try:
            save_diagnostics(page, error)
        except Exception as diagnostic_error:
            print(f'Não foi possível salvar o diagnóstico: {diagnostic_error}')
        if restriction is not None:
            raise restriction
        if page.is_closed():
            raise StopRun('O navegador ou a aba foi fechado.') from error
        if clicked:
            print(f'Ação sem confirmação: {error}. Registrada para revisão; seguindo para o próximo perfil.')
        else:
            print(f'Perfil pulado sem ação: {error}. Seguindo para o próximo perfil.')
    return clicked


def main():
    global _SESSION_GUARD
    if not isinstance(PESSOAS_POR_LOTE, int) or PESSOAS_POR_LOTE < 1:
        raise ValueError('PESSOAS_POR_LOTE deve ser um número inteiro maior que zero.')
    if not isinstance(PAUSA_SEGUNDOS, (int, float)) or PAUSA_SEGUNDOS < 0:
        raise ValueError('PAUSA_SEGUNDOS deve ser um número maior ou igual a zero.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tabela', type=Path, nargs='?')
    parser.add_argument('--login', action='store_true', help='Abre as contas para login manual e salva a sessão.')
    parser.add_argument('--diagnosticar', metavar='URL', help='Abre um perfil e salva diagnóstico sem conectar nem seguir.')
    parser.add_argument('--retomar-erros', action='store_true', help='Tenta novamente apenas falhas registradas antes de iniciar qualquer clique.')
    parser.add_argument('--validar', action='store_true', help='Confere a tabela sem abrir o navegador nem clicar.')
    parser.add_argument('--revisao', type=Path, default=Path(__file__).resolve().parent / 'revisao_dados.json')
    parser.add_argument('--perfil', type=Path, default=Path(__file__).resolve().parent / 'perfil_automacao')
    parser.add_argument('--estado', type=Path, default=Path(__file__).resolve().parent / 'resultados.json')
    parser.add_argument('--navegador', choices=['chrome', 'msedge', 'chromium'], default='chrome')
    args = parser.parse_args()
    if not args.login and not args.diagnosticar and not args.tabela:
        parser.error('Informe a tabela ou use --login.')
    if args.diagnosticar:
        host = (urlparse(args.diagnosticar).hostname or '').lower()
        platform = 'linkedin' if host in {'linkedin.com', 'www.linkedin.com'} else 'github'
        args.diagnosticar = normalize_url(args.diagnosticar, platform)
    issues = []
    people = [] if args.login or args.diagnosticar else read_people(args.tabela, issues)
    if not args.login and not args.diagnosticar:
        summary = {'pessoas_com_perfil_valido': len(people),
                   'perfis_linkedin': sum('linkedin' in p for p in people),
                   'perfis_github': sum('github' in p for p in people),
                   'entradas_para_revisar': issues}
        args.revisao.parent.mkdir(parents=True, exist_ok=True)
        args.revisao.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'{len(people)} pessoas com pelo menos um perfil válido.')
        print(f'{len(issues)} entradas ignoradas para revisão em {args.revisao}.')
        if args.validar:
            print(json.dumps(summary, ensure_ascii=True, indent=2))
            return
    state = json.loads(args.estado.read_text(encoding='utf-8')) if args.estado.exists() else {}
    if args.retomar_erros:
        state = {url: record for url, record in state.items() if record.get('status') not in {'revisar_erro', 'revisar_layout', 'sessao_encerrada'}}
    people = [p for p in people if any(url not in state for key, url in p.items() if key != 'nome')]
    if not args.login and not args.diagnosticar and not people:
        print('Nenhum perfil novo a processar.')
        return
    from playwright.sync_api import sync_playwright
    args.perfil.mkdir(parents=True, exist_ok=True)
    args.estado.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.perfil.resolve()), headless=False,
            channel=None if args.navegador == 'chromium' else args.navegador,
        )
        try:
            page = context.new_page()
            if args.diagnosticar:
                page.goto(args.diagnosticar, wait_until='domcontentloaded', timeout=60000)
                input('Aguarde a página carregar. Depois pressione Enter aqui para salvar o diagnóstico, sem cliques automáticos. ')
                save_diagnostics(page, 'Diagnóstico manual, sem ações de conexão ou seguir.')
                return
            if args.login:
                page.goto('https://www.linkedin.com/login')
                context.new_page().goto('https://github.com/login')
                input('Entre nas duas contas nas abas abertas e pressione Enter aqui para salvar e fechar. ')
                return
            pages = {'linkedin': page, 'github': context.new_page()}
            _SESSION_GUARD = SessionGuard(list(pages.values()))
            last_action = None
            for start in range(0, len(people), PESSOAS_POR_LOTE):
                batch = people[start:start + PESSOAS_POR_LOTE]
                print(f'\nLote {start // PESSOAS_POR_LOTE + 1}: {len(batch)} pessoas')
                for person in batch:
                    for platform in ('linkedin', 'github'):
                        guard_sessions()
                        page = pages[platform]
                        url = person.get(platform)
                        if not url:
                            continue
                        elif url in state:
                            print(f'Pulando {url}: já registrado como {state[url]["status"]}.')
                            continue
                        if last_action is not None:
                            remaining = PAUSA_SEGUNDOS - (time.monotonic() - last_action)
                            if remaining > 0:
                                print(f'Aguardando completar a pausa de {PAUSA_SEGUNDOS:g} segundos...')
                                pause_with_session_checks(remaining)
                        print(f'{person["nome"]}: {url}')
                        if process_profile(page, platform, url, state, args.estado):
                            last_action = time.monotonic()
                if start + PESSOAS_POR_LOTE < len(people):
                    if input(f'Lote finalizado. Continuar com até {PESSOAS_POR_LOTE} pessoas? [s/N] ').strip().lower() != 's':
                        break
        finally:
            _SESSION_GUARD = None
            context.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nInterrompido. Confira resultados.json antes de retomar.')
    except Exception as error:
        print(f'Interrompido: {error}')
        raise SystemExit(1)
