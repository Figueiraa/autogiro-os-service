"""Validação de CPF — regra de domínio da autenticação da Fase 3."""

import pytest

from app.domain.value_objects import cpf


class TestIsValid:
    @pytest.mark.parametrize(
        "documento",
        [
            "529.982.247-25",
            "52998224725",
            "111.444.777-35",
            "11144477735",
            "390.533.447-05",
            " 529.982.247-25 ",
        ],
    )
    def test_aceita_cpf_valido_com_ou_sem_mascara(self, documento):
        assert cpf.is_valid(documento) is True

    @pytest.mark.parametrize(
        "documento",
        [
            "529.982.247-99",  # dígitos verificadores errados
            "529.982.247-2",  # curto demais
            "529.982.247-250",  # longo demais
            "",
            "abc.def.ghi-jk",
        ],
    )
    def test_rejeita_cpf_invalido(self, documento):
        assert cpf.is_valid(documento) is False

    @pytest.mark.parametrize("digito", list("0123456789"))
    def test_rejeita_sequencia_de_digitos_repetidos(self, digito):
        """Passam no módulo 11, mas são inválidos por convenção da Receita Federal."""
        assert cpf.is_valid(digito * 11) is False

    def test_rejeita_none_sem_estourar(self):
        assert cpf.is_valid(None) is False


class TestNormalize:
    def test_remove_mascara_e_preserva_digitos(self):
        assert cpf.normalize("529.982.247-25") == "52998224725"

    def test_remove_espacos_e_caracteres_diversos(self):
        assert cpf.normalize(" 529 982/247*25 ") == "52998224725"

    def test_none_vira_string_vazia(self):
        assert cpf.normalize(None) == ""


class TestFormatMasked:
    def test_aplica_a_mascara_no_cpf_completo(self):
        assert cpf.format_masked("52998224725") == "529.982.247-25"

    def test_mantem_a_mascara_de_entrada_ja_formatada(self):
        assert cpf.format_masked("529.982.247-25") == "529.982.247-25"

    def test_devolve_apenas_digitos_quando_o_tamanho_nao_bate(self):
        assert cpf.format_masked("123") == "123"
