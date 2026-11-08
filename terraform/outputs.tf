output "project_id" {
  description = "ID do projeto no Neon."
  value       = neon_project.os.id
}

# A aplicação usa SQLAlchemy assíncrono (driver asyncpg).
output "database_url" {
  description = "DATABASE_URL do OS Service (asyncpg)."
  value = format(
    "postgresql+asyncpg://%s:%s@%s/%s?ssl=require",
    var.app_role,
    data.neon_branch_role_password.app.password,
    neon_project.os.database_host,
    var.database_name,
  )
  sensitive = true
}

# A Lambda autogiro-auth usa psycopg. O `search_path=auth` vai na URL: o usuário é
# criado pela API do Neon e o usuário do serviço não tem permissão para alterá-lo.
output "auth_database_url" {
  description = "DATABASE_URL da Lambda autogiro-auth (psycopg, usuário somente leitura)."
  value = format(
    "postgresql://%s:%s@%s/%s?sslmode=require&options=-csearch_path%%3Dauth",
    var.auth_role,
    neon_role.auth_reader.password,
    neon_project.os.database_host,
    var.database_name,
  )
  sensitive = true
}
