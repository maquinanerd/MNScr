#!/usr/bin/env python3
"""
Script interativo para testar o sistema de tokens
"""

import sys
from pathlib import Path

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from app.token_tracker import log_tokens, get_tracker

def print_header(text):
    """Print formatted header"""
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80)

def test_log_tokens():
    """Test logging individual tokens"""
    print_header("🧪 Teste 1: Registrando Tokens")
    
    valores = [
        (100, 200, "Pequena requisição"),
        (500, 1000, "Requisição média"),
        (1000, 2500, "Requisição grande"),
    ]
    
    for i, (entrada, saida, desc) in enumerate(valores, 1):
        sucesso = log_tokens(
            prompt_tokens=entrada,
            completion_tokens=saida,
            api_type="gemini",
            model="gemini-2.5-flash",
            success=True
        )
        
        status = "✅" if sucesso else "❌"
        print(f"  {status} Teste {i}: {desc}")
        print(f"     → Entrada: {entrada}, Saída: {saida}, Total: {entrada+saida}")

def test_failures():
    """Test logging failures"""
    print_header("🧪 Teste 2: Registrando Falhas")
    
    failures = [
        ("429", "Quota excedida"),
        ("503", "Serviço indisponível"),
        ("timeout", "Timeout na requisição"),
    ]
    
    for i, (erro, desc) in enumerate(failures, 1):
        sucesso = log_tokens(
            prompt_tokens=0,
            completion_tokens=0,
            api_type="gemini",
            model="gemini-2.5-flash",
            success=False,
            error_message=f"{erro}: {desc}"
        )
        
        status = "✅" if sucesso else "❌"
        print(f"  {status} Teste {i}: {desc}")

def test_metadata():
    """Test logging with metadata"""
    print_header("🧪 Teste 3: Registrando com Metadados")
    
    sucesso = log_tokens(
        prompt_tokens=250,
        completion_tokens=500,
        api_type="gemini",
        model="gemini-2.5-flash",
        success=True,
        metadata={
            "article_title": "Exemplo de Artigo",
            "category": "tecnologia",
            "processing_time_ms": 3200,
            "language": "português"
        }
    )
    
    status = "✅" if sucesso else "❌"
    print(f"  {status} Registro com metadados: {'OK' if sucesso else 'FALHOU'}")

def test_multiple_models():
    """Test logging with different models"""
    print_header("🧪 Teste 4: Múltiplos Modelos")
    
    modelos = [
        ("gemini-2.5-flash", 150, 350),
        ("gemini-2.5-flash-lite", 100, 200),
        ("gemini-2.0-flash", 200, 400),
    ]
    
    for modelo, entrada, saida in modelos:
        sucesso = log_tokens(
            prompt_tokens=entrada,
            completion_tokens=saida,
            api_type="gemini",
            model=modelo,
            success=True
        )
        
        status = "✅" if sucesso else "❌"
        print(f"  {status} {modelo}: {entrada} entrada, {saida} saída")

def test_multiple_apis():
    """Test logging with different APIs"""
    print_header("🧪 Teste 5: Múltiplas APIs")
    
    apis = [
        ("gemini", "gemini-2.5-flash", 150, 320),
        ("openai", "gpt-4", 200, 450),
        ("anthropic", "claude-3-opus", 180, 380),
    ]
    
    for api_type, modelo, entrada, saida in apis:
        sucesso = log_tokens(
            prompt_tokens=entrada,
            completion_tokens=saida,
            api_type=api_type,
            model=modelo,
            success=True
        )
        
        status = "✅" if sucesso else "❌"
        print(f"  {status} {api_type.upper()}: {modelo}")

def test_stats():
    """Test getting statistics"""
    print_header("🧪 Teste 6: Estatísticas")
    
    tracker = get_tracker()
    summary = tracker.get_summary()
    
    print(f"  📊 Resumo calculado:")
    print(f"     • Total de tokens: {summary['total_tokens']:,}")
    print(f"     • Entrada: {summary['total_prompt_tokens']:,}")
    print(f"     • Saída: {summary['total_completion_tokens']:,}")
    print(f"     • Requisições: {summary['total_requests']:,}")
    print(f"     • Taxa de sucesso: {(summary['successful_requests']/summary['total_requests']*100):.1f}%")

def test_print_summary():
    """Test printing summary"""
    print_header("🧪 Teste 7: Resumo Formatado")
    
    tracker = get_tracker()
    tracker.print_summary()

def test_log_files():
    """Test that log files exist"""
    print_header("🧪 Teste 8: Validação de Arquivos")
    
    base_path = Path(__file__).parent / 'logs' / 'tokens'
    
    files = [
        ('token_stats.json', 'Estatísticas consolidadas'),
        ('token_debug.log', 'Log de debug'),
    ]
    
    # Check for JSONL files
    jsonl_files = list(base_path.glob('tokens_*.jsonl'))
    
    print(f"  ✅ Diretório de logs: {base_path}")
    print(f"\n  Arquivos encontrados:")
    
    for filename, desc in files:
        filepath = base_path / filename
        exists = "✅" if filepath.exists() else "❌"
        size = f" ({filepath.stat().st_size} bytes)" if filepath.exists() else ""
        print(f"     {exists} {filename} - {desc}{size}")
    
    if jsonl_files:
        print(f"\n  Arquivos JSONL de logs:")
        for f in jsonl_files:
            with open(f, 'r') as file:
                lines = len(file.readlines())
            print(f"     ✅ {f.name} ({lines} registros)")
    else:
        print(f"\n     ⚠️  Nenhum arquivo JSONL encontrado ainda")

def show_menu():
    """Show test menu"""
    print("\n" + "="*80)
    print("  🧪 TESTES DO SISTEMA DE TOKENS")
    print("="*80)
    print("\nEscolha um teste:")
    print("  1. 📝 Registrar tokens")
    print("  2. ❌ Registrar falhas")
    print("  3. 📎 Registrar com metadados")
    print("  4. 🤖 Teste com múltiplos modelos")
    print("  5. 🔌 Teste com múltiplas APIs")
    print("  6. 📊 Exibir estatísticas")
    print("  7. 📈 Exibir resumo formatado")
    print("  8. 📂 Validar arquivos")
    print("  0. ❌ Sair")
    print("\n  A. 🚀 Executar todos os testes")
    
    return input("\nEscolha uma opção: ").strip()

def run_all_tests():
    """Run all tests"""
    print_header("🚀 EXECUTANDO TODOS OS TESTES")
    
    tests = [
        ("Registrando tokens", test_log_tokens),
        ("Registrando falhas", test_failures),
        ("Com metadados", test_metadata),
        ("Múltiplos modelos", test_multiple_models),
        ("Múltiplas APIs", test_multiple_apis),
        ("Estatísticas", test_stats),
        ("Resumo formatado", test_print_summary),
        ("Validação de arquivos", test_log_files),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"\n❌ ERRO em '{name}': {e}")
            failed += 1
    
    print_header("📊 RESULTADO DOS TESTES")
    print(f"\n  ✅ Testes bem-sucedidos: {passed}")
    print(f"  ❌ Testes falhados: {failed}")
    print(f"  📈 Total: {passed + failed}")
    
    if failed == 0:
        print("\n  🎉 TODOS OS TESTES PASSARAM!")
    else:
        print(f"\n  ⚠️  {failed} teste(s) falharam")

def main():
    """Main menu loop"""
    while True:
        choice = show_menu()
        
        try:
            if choice == '1':
                test_log_tokens()
            elif choice == '2':
                test_failures()
            elif choice == '3':
                test_metadata()
            elif choice == '4':
                test_multiple_models()
            elif choice == '5':
                test_multiple_apis()
            elif choice == '6':
                test_stats()
            elif choice == '7':
                test_print_summary()
            elif choice == '8':
                test_log_files()
            elif choice.upper() == 'A':
                run_all_tests()
            elif choice == '0':
                print("\n👋 Até logo!")
                break
            else:
                print("\n❌ Opção inválida!")
            
            input("\nPressione ENTER para continuar...")
        
        except Exception as e:
            print(f"\n❌ Erro: {e}")
            import traceback
            traceback.print_exc()
            input("\nPressione ENTER para continuar...")

if __name__ == '__main__':
    main()
