"""Java 샘플 레포에서 워커가 뽑은 심볼의 줄 범위를 고정 커밋에서 대조합니다 (#182).

기대값은 같은 커밋의 `src/main/java`를 Java 컴파일러(javac의 구문 트리 API)로 읽어 구한 줄 범위입니다.
시작 줄은 마지막 어노테이션 뒤의 첫 글자가 있는 줄, 끝 줄은 선언의 마지막 글자가 있는 줄입니다.
워커(tree-sitter)와 다른 도구로 구한 값이라, 워커의 규칙이 바뀌어 줄이 어긋나면 여기서 드러납니다.
레포는 Spring 공식 예제(Apache 2.0)이고, 원본 코드는 저장소에 넣지 않습니다.

수동 실행: uv run pytest -m network worker/tests/test_java_sample_match.py
"""

from pathlib import Path

import pytest

from gitinsight_worker.candidates import has_enough_logic, select_candidates
from gitinsight_worker.git import git_cloner
from gitinsight_worker.stage1 import Stage1Result, analyze_tree
from gitinsight_worker.stage2 import read_symbol_code

pytestmark = pytest.mark.network

REPO = "spring-projects/spring-petclinic"
COMMIT = "500158f732419217507c7656904b8e6aa1bcc0d6"
PREFIX = "src/main/java/org/springframework/samples/petclinic/"

# `src/main/java` 아래 symbol_id별 (시작 줄, 끝 줄)입니다. 키는 PREFIX를 뗀 경로입니다.
EXPECTED: dict[str, tuple[int, int]] = {
    "PetClinicApplication.java::PetClinicApplication": (30, 36),
    "PetClinicApplication.java::PetClinicApplication.main": (32, 34),
    "PetClinicRuntimeHints.java::PetClinicRuntimeHints": (25, 38),
    "PetClinicRuntimeHints.java::PetClinicRuntimeHints.registerHints": (28, 36),
    "model/BaseEntity.java::BaseEntity": (33, 51),
    "model/BaseEntity.java::BaseEntity.getId": (39, 41),
    "model/BaseEntity.java::BaseEntity.isNew": (47, 49),
    "model/BaseEntity.java::BaseEntity.setId": (43, 45),
    "model/NamedEntity.java::NamedEntity": (31, 51),
    "model/NamedEntity.java::NamedEntity.getName": (37, 39),
    "model/NamedEntity.java::NamedEntity.setName": (41, 43),
    "model/NamedEntity.java::NamedEntity.toString": (46, 49),
    "model/Person.java::Person": (29, 57),
    "model/Person.java::Person.getFirstName": (41, 43),
    "model/Person.java::Person.getLastName": (49, 51),
    "model/Person.java::Person.setFirstName": (45, 47),
    "model/Person.java::Person.setLastName": (53, 55),
    "owner/Owner.java::Owner": (50, 188),
    "owner/Owner.java::Owner.addPet": (100, 113),
    "owner/Owner.java::Owner.addVisit": (176, 186),
    "owner/Owner.java::Owner.getAddress": (72, 74),
    "owner/Owner.java::Owner.getCity": (80, 82),
    "owner/Owner.java::Owner.getPet": (120, 122),
    "owner/Owner.java::Owner.getPets": (96, 98),
    "owner/Owner.java::Owner.getTelephone": (88, 90),
    "owner/Owner.java::Owner.setAddress": (76, 78),
    "owner/Owner.java::Owner.setCity": (84, 86),
    "owner/Owner.java::Owner.setTelephone": (92, 94),
    "owner/Owner.java::Owner.toString": (160, 169),
    "owner/OwnerController.java::OwnerController": (49, 188),
    "owner/OwnerController.java::OwnerController.OwnerController": (55, 57),
    "owner/OwnerController.java::OwnerController.addPaginationModel": (132, 139),
    "owner/OwnerController.java::OwnerController.findOwner": (65, 70),
    "owner/OwnerController.java::OwnerController.findPaginatedForOwnersLastName": (141, 146),
    "owner/OwnerController.java::OwnerController.initCreationForm": (73, 75),
    "owner/OwnerController.java::OwnerController.initFindForm": (90, 92),
    "owner/OwnerController.java::OwnerController.initUpdateOwnerForm": (149, 151),
    "owner/OwnerController.java::OwnerController.processCreationForm": (78, 87),
    "owner/OwnerController.java::OwnerController.processFindForm": (95, 130),
    "owner/OwnerController.java::OwnerController.processUpdateOwnerForm": (154, 171),
    "owner/OwnerController.java::OwnerController.setAllowedFields": (60, 62),
    "owner/OwnerController.java::OwnerController.showOwner": (179, 186),
    "owner/OwnerRepository.java::OwnerRepository": (36, 62),
    "owner/Pet.java::Pet": (46, 85),
    "owner/Pet.java::Pet.addVisit": (81, 83),
    "owner/Pet.java::Pet.getBirthDate": (65, 67),
    "owner/Pet.java::Pet.getType": (69, 71),
    "owner/Pet.java::Pet.getVisits": (77, 79),
    "owner/Pet.java::Pet.setBirthDate": (61, 63),
    "owner/Pet.java::Pet.setType": (73, 75),
    "owner/PetController.java::PetController": (49, 207),
    "owner/PetController.java::PetController.PetController": (57, 60),
    "owner/PetController.java::PetController.findOwner": (68, 73),
    "owner/PetController.java::PetController.findPet": (76, 87),
    "owner/PetController.java::PetController.initCreationForm": (101, 105),
    "owner/PetController.java::PetController.initOwnerBinder": (90, 92),
    "owner/PetController.java::PetController.initPetBinder": (95, 98),
    "owner/PetController.java::PetController.initUpdateForm": (140, 142),
    "owner/PetController.java::PetController.isDuplicatePetNameViolation": (202, 205),
    "owner/PetController.java::PetController.populatePetTypes": (63, 65),
    "owner/PetController.java::PetController.processCreationForm": (108, 137),
    "owner/PetController.java::PetController.processUpdateForm": (145, 179),
    "owner/PetController.java::PetController.updatePetDetails": (186, 200),
    "owner/PetType.java::PetType": (28, 30),
    "owner/PetTypeFormatter.java::PetTypeFormatter": (37, 62),
    "owner/PetTypeFormatter.java::PetTypeFormatter.PetTypeFormatter": (41, 43),
    "owner/PetTypeFormatter.java::PetTypeFormatter.parse": (52, 60),
    "owner/PetTypeFormatter.java::PetTypeFormatter.print": (46, 49),
    "owner/PetTypeRepository.java::PetTypeRepository": (30, 39),
    "owner/PetValidator.java::PetValidator": (32, 69),
    "owner/PetValidator.java::PetValidator.supports": (65, 67),
    "owner/PetValidator.java::PetValidator.validate": (39, 59),
    "owner/Visit.java::Visit": (37, 70),
    "owner/Visit.java::Visit.Visit": (50, 52),
    "owner/Visit.java::Visit.getDate": (54, 56),
    "owner/Visit.java::Visit.getDescription": (62, 64),
    "owner/Visit.java::Visit.setDate": (58, 60),
    "owner/Visit.java::Visit.setDescription": (66, 68),
    "owner/VisitController.java::VisitController": (43, 114),
    "owner/VisitController.java::VisitController.VisitController": (47, 49),
    "owner/VisitController.java::VisitController.initNewVisitForm": (91, 93),
    "owner/VisitController.java::VisitController.loadPetWithVisit": (64, 81),
    "owner/VisitController.java::VisitController.minVisitDate": (84, 86),
    "owner/VisitController.java::VisitController.processNewVisitForm": (98, 112),
    "owner/VisitController.java::VisitController.setAllowedFields": (52, 54),
    "system/CacheConfiguration.java::CacheConfiguration": (33, 53),
    "system/CacheConfiguration.java::CacheConfiguration.cacheConfiguration": (49, 51),
    "system/CacheConfiguration.java::CacheConfiguration.petclinicCacheConfigurationCustomizer": (
        36,
        38,
    ),
    "system/CrashController.java::CrashController": (29, 37),
    "system/CrashController.java::CrashController.triggerException": (32, 35),
    "system/WebConfiguration.java::WebConfiguration": (25, 60),
    "system/WebConfiguration.java::WebConfiguration.addInterceptors": (56, 58),
    "system/WebConfiguration.java::WebConfiguration.localeChangeInterceptor": (45, 49),
    "system/WebConfiguration.java::WebConfiguration.localeResolver": (33, 37),
    "system/WelcomeController.java::WelcomeController": (23, 30),
    "system/WelcomeController.java::WelcomeController.welcome": (26, 28),
    "vet/Specialty.java::Specialty": (30, 32),
    "vet/Vet.java::Vet": (45, 74),
    "vet/Vet.java::Vet.addSpecialty": (70, 72),
    "vet/Vet.java::Vet.getNrOfSpecialties": (66, 68),
    "vet/Vet.java::Vet.getSpecialties": (60, 64),
    "vet/Vet.java::Vet.getSpecialtiesInternal": (52, 57),
    "vet/VetController.java::VetController": (37, 81),
    "vet/VetController.java::VetController.VetController": (41, 43),
    "vet/VetController.java::VetController.addPaginationModel": (56, 63),
    "vet/VetController.java::VetController.findPaginated": (65, 70),
    "vet/VetController.java::VetController.showResourcesVetList": (73, 79),
    "vet/VetController.java::VetController.showVetList": (46, 54),
    "vet/VetRepository.java::VetRepository": (38, 58),
    "vet/Vets.java::Vets": (31, 43),
    "vet/Vets.java::Vets.getVetList": (36, 41),
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clone") / "repo"
    git_cloner(f"https://github.com/{REPO}.git", COMMIT)(path)
    return path


@pytest.fixture(scope="module")
def result(repo: Path) -> Stage1Result:
    return analyze_tree(repo, COMMIT)


def test_every_main_symbol_has_the_same_range_as_javac(result: Stage1Result) -> None:
    extracted = {
        s.symbol_id.removeprefix(PREFIX): (s.start_line, s.end_line)
        for s in result.symbols
        if s.path.startswith(PREFIX)
    }

    assert extracted == EXPECTED
    assert len(EXPECTED) == 111
    assert {s.commit_sha for s in result.symbols} == {COMMIT}


def test_ranges_start_at_the_declaration_not_the_annotation(
    repo: Path, result: Stage1Result
) -> None:
    for symbol in result.symbols:
        lines = (repo / symbol.path).read_text(encoding="utf-8").splitlines()
        first = lines[symbol.start_line - 1].strip()
        assert not first.startswith("@"), symbol.symbol_id
        assert lines[symbol.end_line - 1].strip().endswith("}"), symbol.symbol_id


def test_language_and_test_code(result: Stage1Result) -> None:
    assert result.languages[0].language == "Java"
    # 테스트 코드(src/test)도 뽑지만 후보에서는 빠집니다.
    assert any(s.path.startswith("src/test/") for s in result.symbols)
    assert result.references == {}


def test_candidates_skip_entities_and_keep_controllers(repo: Path, result: Stage1Result) -> None:
    # 모든 코드가 `samples` 패키지 아래에 있어도 예제 코드로 빼지 않습니다.
    candidates = select_candidates(result.symbols)
    usable = [
        c.symbol.symbol_id.removeprefix(PREFIX)
        for c in candidates
        if has_enough_logic(read_symbol_code(repo, c), c.symbol.path)
    ]

    assert not any(c.startswith("src/test/") for c in usable)
    assert usable[:3] == [
        "owner/PetController.java::PetController",
        "owner/OwnerController.java::OwnerController",
        "owner/Owner.java::Owner",
    ]
    # 필드와 getter·setter뿐인 엔티티, 메서드 선언뿐인 리포지토리는 주제가 되지 않습니다.
    for hollow in (
        "owner/Pet.java::Pet",
        "owner/Visit.java::Visit",
        "model/Person.java::Person",
        "model/BaseEntity.java::BaseEntity",
        "owner/OwnerRepository.java::OwnerRepository",
    ):
        assert hollow not in usable
