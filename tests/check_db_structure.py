import sqlite3

conn = sqlite3.connect('data/app.db')
cursor = conn.cursor()

# Verificar esquema da tabela posts
cursor.execute('PRAGMA table_info(posts)')
columns = cursor.fetchall()

print('Colunas da tabela posts:')
for col in columns:
    print(f'  {col[1]} ({col[2]})')

# Contar registros
cursor.execute('SELECT COUNT(*) FROM posts')
print(f'\nTotal de registros: {cursor.fetchone()[0]}')

# Mostrar últimos registros por data
cursor.execute('''
SELECT DATE(created_at) as data, COUNT(*) as qtd
FROM posts
GROUP BY DATE(created_at)
ORDER BY data DESC
LIMIT 10
''')

print(f'\nPosts por data (mais recentes):')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]} posts')

conn.close()
