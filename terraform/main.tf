# Banco próprio do OS Service: um projeto Neon só dele, com credenciais que nenhum
# outro serviço recebe.
resource "neon_project" "os" {
  name                      = var.project_name
  org_id                    = var.org_id
  region_id                 = var.region
  pg_version                = var.pg_version
  history_retention_seconds = var.history_retention_seconds

  branch {
    name          = "main"
    database_name = var.database_name
    role_name     = var.app_role
  }
}

# Usuário da Lambda de autenticação. As permissões (só a view auth.clients) são
# dadas pela migration 002 do serviço, que roda depois deste apply.
resource "neon_role" "auth_reader" {
  project_id = neon_project.os.id
  branch_id  = neon_project.os.default_branch_id
  name       = var.auth_role
}

data "neon_branch_role_password" "app" {
  project_id = neon_project.os.id
  branch_id  = neon_project.os.default_branch_id
  role_name  = var.app_role
}
