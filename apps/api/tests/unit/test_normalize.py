"""Unit tests for relayiq.canonical.normalize — pure normalization primitives."""

from datetime import UTC, datetime

from relayiq.canonical.normalize import (
    EMPLOYEE_RANGES,
    derive_department,
    derive_seniority,
    employee_count_to_range,
    extract_root_domain,
    industries_equivalent,
    is_valid_domain,
    is_valid_email_syntax,
    normalize_company_name,
    normalize_email,
    normalize_industry,
    normalize_job_title,
    normalize_value,
    ranges_adjacent,
    titles_equivalent,
    validate_field,
    values_equivalent,
)


class TestNormalizeCompanyName:
    def test_strips_inc_suffix(self) -> None:
        assert normalize_company_name("Acme Inc.") == "acme"

    def test_strips_corporation_suffix(self) -> None:
        assert normalize_company_name("Acme Corporation") == "acme"

    def test_inc_and_corporation_normalize_equal(self) -> None:
        assert normalize_company_name("Acme Inc.") == normalize_company_name("Acme Corporation")

    def test_various_legal_suffixes(self) -> None:
        assert normalize_company_name("Globex L.L.C.") == "globex"
        assert normalize_company_name("Initech Ltd") == "initech"
        assert normalize_company_name("Umbrella S.A.") == "umbrella"
        assert normalize_company_name("Wayne GmbH") == "wayne"
        assert normalize_company_name("Stark PLC") == "stark"
        assert normalize_company_name("Acme, Incorporated") == "acme"

    def test_strips_stacked_trailing_suffixes(self) -> None:
        assert normalize_company_name("Initech Holdings Group") == "initech"
        assert normalize_company_name("Acme Group Inc.") == "acme"

    def test_ampersand_co(self) -> None:
        assert normalize_company_name("Tyrell & Co.") == "tyrell"

    def test_suffix_only_name_is_preserved(self) -> None:
        assert normalize_company_name("Company") == "company"

    def test_leading_suffix_word_not_stripped(self) -> None:
        assert normalize_company_name("SA Recycling") == "sa recycling"

    def test_lowercase_and_whitespace_collapse(self) -> None:
        assert normalize_company_name("  ACME   WIDGETS  INC ") == "acme widgets"

    def test_unicode_preserved(self) -> None:
        assert normalize_company_name("Müller GmbH") == "müller"

    def test_none_and_empty(self) -> None:
        assert normalize_company_name(None) is None
        assert normalize_company_name("") is None
        assert normalize_company_name("   ") is None
        assert normalize_company_name("...") is None


class TestExtractRootDomain:
    def test_bare_domain(self) -> None:
        assert extract_root_domain("acme.com") == "acme.com"

    def test_url_with_scheme_path_query(self) -> None:
        assert extract_root_domain("https://www.acme.com/about?ref=x#top") == "acme.com"

    def test_subdomains_collapse_to_root(self) -> None:
        assert extract_root_domain("https://app.staging.acme.com") == "acme.com"

    def test_port_stripped(self) -> None:
        assert extract_root_domain("acme.com:8080") == "acme.com"
        assert extract_root_domain("https://acme.com:8443/x") == "acme.com"

    def test_two_part_public_suffixes(self) -> None:
        assert extract_root_domain("http://acme.co.uk") == "acme.co.uk"
        assert extract_root_domain("sub.acme.co.jp") == "acme.co.jp"
        assert extract_root_domain("www.acme.com.au") == "acme.com.au"
        assert extract_root_domain("deep.sub.acme.org.uk") == "acme.org.uk"

    def test_bare_public_suffix_is_invalid(self) -> None:
        assert extract_root_domain("co.uk") is None

    def test_case_and_trailing_dot(self) -> None:
        assert extract_root_domain("ACME.COM") == "acme.com"
        assert extract_root_domain("acme.io.") == "acme.io"

    def test_distinct_roots_stay_distinct(self) -> None:
        assert extract_root_domain("acme.com") != extract_root_domain("getacme.com")

    def test_invalid_inputs(self) -> None:
        assert extract_root_domain(None) is None
        assert extract_root_domain("") is None
        assert extract_root_domain("nodot") is None
        assert extract_root_domain("has space.com") is None
        assert extract_root_domain("192.168.0.1") is None
        assert extract_root_domain("https://192.168.0.1:8080/admin") is None
        assert extract_root_domain("münchen.de") is None
        assert extract_root_domain("bad_chars.com") is None


class TestIsValidDomain:
    def test_valid(self) -> None:
        assert is_valid_domain("acme.com") is True
        assert is_valid_domain("sub.acme.co.uk") is True
        assert is_valid_domain("acme.test") is True
        assert is_valid_domain("widget.example") is True
        assert is_valid_domain("xn--bcher-kva.com") is True
        assert is_valid_domain("ACME.COM") is True

    def test_invalid(self) -> None:
        assert is_valid_domain(None) is False
        assert is_valid_domain("") is False
        assert is_valid_domain("nodot") is False
        assert is_valid_domain("-acme.com") is False
        assert is_valid_domain("acme-.com") is False
        assert is_valid_domain("ac me.com") is False
        assert is_valid_domain("acme.c") is False
        assert is_valid_domain("acme.c0m") is False
        assert is_valid_domain("192.168.1.1") is False
        assert is_valid_domain("under_score.com") is False
        assert is_valid_domain(("a" * 64) + ".com") is False
        assert is_valid_domain(".".join(["a" * 60] * 5)) is False  # > 253 chars total


class TestEmail:
    def test_normalize_trims_and_lowercases(self) -> None:
        assert normalize_email("  John.Doe@Acme.COM ") == "john.doe@acme.com"

    def test_normalize_invalid_returns_none(self) -> None:
        assert normalize_email("not-an-email") is None
        assert normalize_email("a@b@c.com") is None
        assert normalize_email(None) is None
        assert normalize_email("") is None

    def test_syntax_valid(self) -> None:
        assert is_valid_email_syntax("jane@acme.test") is True
        assert is_valid_email_syntax("user+tag@acme.co.uk") is True
        assert is_valid_email_syntax("first.last@sub.acme.com") is True

    def test_syntax_invalid(self) -> None:
        assert is_valid_email_syntax(None) is False
        assert is_valid_email_syntax("") is False
        assert is_valid_email_syntax("no-at-sign") is False
        assert is_valid_email_syntax("a@b@c.com") is False
        assert is_valid_email_syntax("user@notld") is False
        assert is_valid_email_syntax("user@192.168.1.1") is False
        assert is_valid_email_syntax(".leading@acme.com") is False
        assert is_valid_email_syntax("dou..ble@acme.com") is False
        assert is_valid_email_syntax("@acme.com") is False
        assert is_valid_email_syntax(("x" * 65) + "@acme.com") is False


class TestNormalizeJobTitle:
    def test_vp_expansion(self) -> None:
        assert normalize_job_title("VP Sales") == "vice president sales"

    def test_already_expanded_untouched(self) -> None:
        assert normalize_job_title("Vice President of Revenue") == "vice president of revenue"

    def test_dotted_abbreviations(self) -> None:
        assert normalize_job_title("V.P. of Sales") == "vice president of sales"
        assert normalize_job_title("C.T.O.") == "cto"

    def test_svp_evp_sr_jr(self) -> None:
        assert normalize_job_title("SVP Engineering") == "senior vice president engineering"
        assert normalize_job_title("EVP Marketing") == "executive vice president marketing"
        assert normalize_job_title("Sr. Eng Mgr") == "senior engineering manager"
        assert normalize_job_title("Jr Developer") == "junior developer"
        assert normalize_job_title("Dir of Product") == "director of product"

    def test_c_suite_kept_as_is(self) -> None:
        assert normalize_job_title("CEO") == "ceo"
        assert normalize_job_title("CISO") == "ciso"

    def test_parenthetical_suffix_stripped(self) -> None:
        assert normalize_job_title("VP Sales (EMEA)") == "vice president sales"

    def test_whitespace_collapse_and_none(self) -> None:
        assert normalize_job_title("  Marketing   Manager ") == "marketing manager"
        assert normalize_job_title(None) is None
        assert normalize_job_title("") is None
        assert normalize_job_title("(interim)") is None


class TestTitlesEquivalent:
    def test_vp_sales_matches_vice_president_of_sales(self) -> None:
        assert titles_equivalent("VP Sales", "Vice President of Sales") is True

    def test_vp_sales_does_not_match_vp_revenue(self) -> None:
        # Documented choice: same seniority, but function tokens differ (sales vs revenue).
        assert titles_equivalent("VP Sales", "Vice President of Revenue") is False

    def test_exact_after_normalization(self) -> None:
        assert titles_equivalent("VP Sales", "vp   sales") is True

    def test_head_of_vs_director(self) -> None:
        assert titles_equivalent("Head of Sales", "Sales Director") is True

    def test_pure_rank_titles(self) -> None:
        assert titles_equivalent("CEO", "Chief Executive Officer") is True

    def test_different_departments(self) -> None:
        assert titles_equivalent("VP Sales", "VP Marketing") is False

    def test_different_seniority(self) -> None:
        assert titles_equivalent("Software Engineer", "Senior Software Engineer") is False

    def test_none_never_equivalent(self) -> None:
        assert titles_equivalent(None, "VP Sales") is False
        assert titles_equivalent("VP Sales", None) is False
        assert titles_equivalent(None, None) is False


class TestDeriveSeniority:
    def test_c_level(self) -> None:
        assert derive_seniority("Chief Revenue Officer") == "c_level"
        assert derive_seniority("CTO") == "c_level"
        assert derive_seniority("President") == "c_level"
        assert derive_seniority("Co-Founder & CEO") == "c_level"  # most-senior pattern wins

    def test_founder(self) -> None:
        assert derive_seniority("Founder") == "founder"
        assert derive_seniority("Co-Founder") == "founder"

    def test_vp(self) -> None:
        assert derive_seniority("VP Sales") == "vp"
        assert derive_seniority("SVP Marketing") == "vp"
        assert derive_seniority("Vice President") == "vp"

    def test_director(self) -> None:
        assert derive_seniority("Director, Product") == "director"
        assert derive_seniority("Head of Engineering") == "director"

    def test_manager(self) -> None:
        assert derive_seniority("Engineering Manager") == "manager"
        assert derive_seniority("Senior Manager, Ops") == "manager"

    def test_senior_ic(self) -> None:
        assert derive_seniority("Senior Software Engineer") == "senior_ic"
        assert derive_seniority("Staff Engineer") == "senior_ic"
        assert derive_seniority("Principal Data Scientist") == "senior_ic"

    def test_ic_and_unknown(self) -> None:
        assert derive_seniority("Software Engineer") == "ic"
        assert derive_seniority("Account Executive") == "ic"
        assert derive_seniority(None) == "unknown"
        assert derive_seniority("") == "unknown"


class TestDeriveDepartment:
    def test_sales(self) -> None:
        assert derive_department("VP Sales") == "sales"
        assert derive_department("Account Executive") == "sales"
        assert derive_department("SDR") == "sales"
        assert derive_department("BDR") == "sales"

    def test_revops_beats_sales_and_operations(self) -> None:
        assert derive_department("Revenue Operations Manager") == "revops"
        assert derive_department("Rev Ops Analyst") == "revops"
        assert derive_department("Director of Sales Operations") == "revops"
        assert derive_department("Sales Ops") == "revops"

    def test_marketing(self) -> None:
        assert derive_department("CMO") == "marketing"
        assert derive_department("Product Marketing Manager") == "marketing"

    def test_engineering(self) -> None:
        assert derive_department("Software Engineer") == "engineering"
        assert derive_department("Eng Manager") == "engineering"

    def test_product(self) -> None:
        assert derive_department("Product Manager") == "product"

    def test_finance_hr_operations(self) -> None:
        assert derive_department("CFO") == "finance"
        assert derive_department("Controller") == "finance"
        assert derive_department("Head of People") == "hr"
        assert derive_department("Recruiter") == "hr"
        assert derive_department("COO") == "operations"
        assert derive_department("Operations Manager") == "operations"

    def test_customer_success_it_legal(self) -> None:
        assert derive_department("Customer Success Manager") == "customer_success"
        assert derive_department("CISO") == "it"
        assert derive_department("General Counsel") == "legal"

    def test_unknown(self) -> None:
        assert derive_department("Basket Weaver") == "unknown"
        assert derive_department("Vice President of Revenue") == "unknown"
        assert derive_department(None) == "unknown"


class TestNormalizeIndustry:
    def test_software_aliases(self) -> None:
        assert normalize_industry("Computer Software") == "software"
        assert normalize_industry("SaaS") == "software"
        assert normalize_industry("Information Technology") == "software"
        assert normalize_industry("IT Services") == "software"

    def test_other_canonical_buckets(self) -> None:
        assert normalize_industry("FinTech") == "financial_services"
        assert normalize_industry("Banking") == "financial_services"
        assert normalize_industry("Health Care") == "healthcare"
        assert normalize_industry("Hospitals") == "healthcare"
        assert normalize_industry("E-Commerce") == "retail"
        assert normalize_industry("ecommerce") == "retail"
        assert normalize_industry("Industrial") == "manufacturing"
        assert normalize_industry("Consulting") == "professional_services"
        assert normalize_industry("Education") == "education"
        assert normalize_industry("Real Estate") == "real_estate"
        assert normalize_industry("Publishing") == "media"
        assert normalize_industry("Entertainment") == "media"

    def test_unknown_slugified(self) -> None:
        assert normalize_industry("Telecommunications") == "telecommunications"
        assert normalize_industry("Oil & Gas") == "oil_gas"

    def test_none_and_empty(self) -> None:
        assert normalize_industry(None) is None
        assert normalize_industry("") is None
        assert normalize_industry("   ") is None


class TestIndustriesEquivalent:
    def test_aliases_match(self) -> None:
        assert industries_equivalent("Software", "Information Technology") is True
        assert industries_equivalent("software", "SaaS") is True

    def test_different_buckets(self) -> None:
        assert industries_equivalent("Software", "Banking") is False

    def test_none_never_matches(self) -> None:
        assert industries_equivalent(None, "software") is False
        assert industries_equivalent(None, None) is False


class TestEmployeeRanges:
    def test_bucket_boundaries(self) -> None:
        assert employee_count_to_range(1) == "1-10"
        assert employee_count_to_range(10) == "1-10"
        assert employee_count_to_range(11) == "11-50"
        assert employee_count_to_range(200) == "51-200"
        assert employee_count_to_range(201) == "201-500"
        assert employee_count_to_range(5000) == "1001-5000"
        assert employee_count_to_range(10001) == "10001+"
        assert employee_count_to_range(250000) == "10001+"

    def test_invalid_counts(self) -> None:
        assert employee_count_to_range(0) is None
        assert employee_count_to_range(-5) is None
        assert employee_count_to_range(None) is None

    def test_range_table_shape(self) -> None:
        assert len(EMPLOYEE_RANGES) == 8
        assert EMPLOYEE_RANGES[-1] == (10001, None, "10001+")

    def test_ranges_adjacent(self) -> None:
        assert ranges_adjacent("51-200", "201-500") is True
        assert ranges_adjacent("201-500", "51-200") is True
        assert ranges_adjacent("51-200", "51-200") is True
        assert ranges_adjacent("10001+", "5001-10000") is True
        assert ranges_adjacent("1-10", "51-200") is False
        assert ranges_adjacent("bogus", "1-10") is False
        assert ranges_adjacent(None, "1-10") is False
        assert ranges_adjacent("1-10", None) is False


class TestValuesEquivalent:
    def test_job_title_dispatch(self) -> None:
        assert values_equivalent("job_title", "VP Sales", "Vice President of Sales") is True
        assert values_equivalent("job_title", "VP Sales", "Vice President of Revenue") is False

    def test_industry_dispatch(self) -> None:
        assert values_equivalent("industry", "Software", "Information Technology") is True
        assert values_equivalent("industry", "Software", "Retail") is False

    def test_company_name_dispatch(self) -> None:
        assert values_equivalent("company_name", "Acme Inc.", "Acme Corporation") is True
        assert values_equivalent("name", "Acme Inc", "ACME LLC") is True
        assert values_equivalent("name", "Acme Inc", "Globex Inc") is False

    def test_domain_dispatch(self) -> None:
        assert values_equivalent("company_domain", "https://www.acme.com", "acme.com") is True
        assert values_equivalent("website", "acme.com", "getacme.com") is False
        assert values_equivalent("root_domain", "app.acme.co.uk", "acme.co.uk") is True

    def test_email_dispatch(self) -> None:
        assert values_equivalent("work_email", " John@Acme.com ", "john@acme.com") is True
        assert values_equivalent("work_email", "john@acme.com", "jane@acme.com") is False

    def test_employee_range_exact(self) -> None:
        assert values_equivalent("employee_range", "51-200", "51-200") is True
        assert values_equivalent("employee_range", "51-200", "201-500") is False

    def test_employee_count_tolerance(self) -> None:
        assert values_equivalent("employee_count", "100", "110") is True  # ~9% apart
        assert values_equivalent("employee_count", "100", "115") is True  # exactly 15% of the max
        assert values_equivalent("employee_count", "100", "200") is False
        assert values_equivalent("employee_count", "1,200", "1200") is True

    def test_employee_count_non_parseable_falls_back(self) -> None:
        assert values_equivalent("employee_count", "many", "many") is True
        assert values_equivalent("employee_count", "many", "few") is False

    def test_default_casefold_equality(self) -> None:
        assert values_equivalent("hq_city", "Austin", "austin") is True
        assert values_equivalent("hq_city", "Austin", "Dallas") is False

    def test_none_handling(self) -> None:
        assert values_equivalent("hq_city", None, None) is True
        assert values_equivalent("hq_city", "Austin", None) is False
        assert values_equivalent("job_title", None, None) is True  # None-vs-None short-circuits


class TestNormalizeValue:
    def test_per_field_dispatch(self) -> None:
        assert normalize_value("job_title", "VP Sales") == "vice president sales"
        assert normalize_value("industry", "SaaS") == "software"
        assert normalize_value("company_name", "Acme Inc.") == "acme"
        assert normalize_value("website", "https://www.acme.com/about") == "acme.com"
        assert normalize_value("work_email", "John@ACME.com") == "john@acme.com"

    def test_employee_count_passthrough(self) -> None:
        assert normalize_value("employee_count", " 250 ") == "250"

    def test_default_strip(self) -> None:
        assert normalize_value("hq_city", " Austin ") == "Austin"

    def test_empty_becomes_none(self) -> None:
        assert normalize_value("hq_city", "") is None
        assert normalize_value("hq_city", "   ") is None
        assert normalize_value("hq_city", None) is None


class TestValidateField:
    def test_domain_fields(self) -> None:
        result = validate_field("company_domain", "acme.com")
        assert result["valid"] is True
        assert result["checks"] == {"non_empty": True, "domain_valid": True}
        assert validate_field("website", "not a domain")["valid"] is False

    def test_email_field(self) -> None:
        assert validate_field("work_email", "jane@acme.test")["valid"] is True
        assert validate_field("work_email", "bogus")["valid"] is False
        assert validate_field("work_email", None)["valid"] is False

    def test_employee_count(self) -> None:
        assert validate_field("employee_count", "250")["valid"] is True
        assert validate_field("employee_count", "0")["valid"] is False
        assert validate_field("employee_count", "-3")["valid"] is False
        assert validate_field("employee_count", "many")["valid"] is False

    def test_founded_year(self) -> None:
        current_year = datetime.now(tz=UTC).year
        assert validate_field("founded_year", "1999")["valid"] is True
        assert validate_field("founded_year", "1600")["valid"] is True
        assert validate_field("founded_year", "1599")["valid"] is False
        assert validate_field("founded_year", str(current_year))["valid"] is True
        assert validate_field("founded_year", str(current_year + 1))["valid"] is False

    def test_linkedin_url(self) -> None:
        assert validate_field("linkedin_url", "https://www.linkedin.com/in/jane")["valid"] is True
        assert validate_field("linkedin_url", "linkedin.com/company/acme")["valid"] is True
        assert validate_field("linkedin_url", "https://evil.com/linkedin.com")["valid"] is False
        assert validate_field("linkedin_url", "https://linkedin.com.evil.com/x")["valid"] is False

    def test_generic_non_empty(self) -> None:
        assert validate_field("hq_city", "Austin")["valid"] is True
        assert validate_field("hq_city", "")["valid"] is False
        assert validate_field("hq_city", None)["valid"] is False
        assert validate_field("some_unknown_field", "value")["valid"] is True

    def test_result_shape(self) -> None:
        result = validate_field("work_email", "jane@acme.test")
        assert set(result.keys()) == {"valid", "checks"}
        assert result["checks"] == {"non_empty": True, "email_syntax": True}
