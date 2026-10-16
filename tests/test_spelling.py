from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.hybrid import search
from lantern.search.spelling import correct


def indexed(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    return conn


def test_an_unknown_word_takes_the_first_guess_the_index_knows(conn, project, embedder):
    indexed(conn, project, embedder)
    # the spell checker's own first choice ("drzwi") is not in the files; "drzewa" is
    assert correct(conn, "przycinanie drzwa", {"drzwa": ["drzwi", "drzewa"]}) == "przycinanie drzewa"
    assert correct(conn, "spotknie", {"spotknie": ["spotkanie"]}) == "spotkanie"


def test_known_words_and_unknown_guesses_leave_the_query_alone(conn, project, embedder):
    indexed(conn, project, embedder)
    assert correct(conn, "decyzyjnego", {"decyzyjnego": ["decyzyjny"]}) is None  # the index knows the word
    assert correct(conn, "warszwie", {"warszwie": ["warstwie", "warszawie"]}) is None  # no guess is known
    assert correct(conn, "spotkanie w lodzi", {"lodzi": ["łodzi"]}) is None  # accents only: search ignores them
    assert correct(conn, "backoff_wraper", {"backoff_wraper": ["backoff_wrapper"]}) is None  # code is not corrected


def test_names_of_apps_count_as_known(conn, project, embedder):
    indexed(conn, project, embedder)
    conn.execute(
        "INSERT INTO apps(path, name, names, keywords, mtime, languages, document, model_id, embedding) "
        "SELECT '/Applications/Calculator.app', 'Kalkulator', '[]', '[]', 0, 'pl', 'Kalkulator', id, x'' "
        "FROM embedding_models LIMIT 1"
    )
    assert correct(conn, "kalkultor", {"kalkultor": ["kalkulator"]}) == "kalkulator"


def test_search_reports_the_corrected_query(conn, project, embedder):
    indexed(conn, project, embedder)
    resp = search(conn, embedder, "przycinanie drzwa", log=False, spelling={"drzwa": ["drzwi", "drzewa"]})
    assert resp.corrected_query == "przycinanie drzewa" and resp.results[0].path.endswith("README.md")
    assert search(conn, embedder, "przycinanie drzewa", log=False).corrected_query is None
