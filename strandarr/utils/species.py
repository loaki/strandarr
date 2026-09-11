import re

SPECIES: tuple[tuple[str, str], ...] = (
    ("Delphinus delphis", "Dauphin commun"),
    ("Stenella coeruleoalba", "Dauphin bleu et blanc"),
    ("Tursiops truncatus", "Grand dauphin"),
    ("Delphinidae", "Delphinidé ind."),
    ("Stenella clymene", "Dauphin clymène"),
    ("Stenella frontalis", "Dauphin tacheté de l'Atlantique"),
    ("Stenella attenuata", "Dauphin tacheté tropical"),
    ("Stenella longirostris", "Dauphin à long bec"),
    ("Lagenodelphis hosei", "Dauphin de Fraser"),
    ("Lagenorhynchus albirostris", "Lagénorhynque à bec blanc"),
    ("Lagenorhynchus acutus", "Lagénorhynque à flancs blancs"),
    ("Cephalorhynchus commersonii", "Dauphin de Commerson"),
    ("Sotalia guianensis", "Sotalie"),
    ("Steno bredanensis", "Sténo"),
    ("Phocoena phocoena", "Marsouin commun"),
    ("Globicephala melas", "Globicéphale noir"),
    ("Globicephala macrorhynchus", "Globicéphale tropical"),
    ("Grampus griseus", "Dauphin de Risso"),
    ("Orcinus orca", "Orque"),
    ("Pseudorca crassidens", "Pseudorque (faux orque)"),
    ("Feresa attenuata", "Orque nain"),
    ("Peponocephala electra", "Péponocéphale (Dauphin d'Electre)"),
    ("Ziphius cavirostris", "Ziphius (baleine à bec de Cuvier)"),
    ("Hyperoodon ampullatus", "Hypérodon boréal"),
    ("Hyperoodon planifrons", "Hypérodon austral"),
    ("Mesoplodon bidens", "Mésoplodon de Sowerby"),
    ("Mesoplodon densirostris", "Mésoplodon de Blainville"),
    ("Mesoplodon europaeus", "Mésoplodon de Gervais"),
    ("Mesoplodon layardii", "Mésoplodon de Layard"),
    ("Mesoplodon mirus", "Mésoplodon de True"),
    ("Indopacetus pacificus", "Mésoplodon de Longman (M. du Chili)"),
    ("Berardius bairdii", "Bérardie Boréale"),
    ("Ziphiidae", "Baleine à bec ind."),
    ("Physeter macrocephalus", "Cachalot"),
    ("Kogia breviceps", "Cachalot pygmée"),
    ("Kogia sima", "Cachalot nain"),
    ("Kogia breviceps / K. sima", "Cachalot nain / pygmée"),
    ("Balaenoptera physalus", "Rorqual commun"),
    ("Balaenoptera acutorostrata", "Petit rorqual (R. à museau pointu)"),
    ("Balaenoptera bonaerensis", "Petit rorqual antarctique"),
    ("Balaenoptera musculus", "Rorqual bleu (baleine bleue)"),
    ("Balaenoptera borealis", "Rorqual boréal (R. de Rudolphi)"),
    ("Balaenoptera edeni", "Rorqual de Bryde (tropical)"),
    ("Balaenopteridae", "Rorqual ind."),
    ("Megaptera novaeangliae", "Baleine à bosse (Jubarte)"),
    ("Eubalaena glacialis", "Baleine franche boréale"),
    ("Eubalaena australis", "Baleine franche australe"),
    ("Eubalaena glacialis/australis", "Baleine franche boréale ou australe"),
    ("Eschrichtius robustus", "Baleine grise"),
    ("Delphinapterus leucas", "Belouga"),
    ("Dugong dugon", "Dugong"),
    ("Trichechus manatus", "Lamantin des Caraïbes"),
    ("Cetacea", "Cétacé ind."),
    ("Halichoerus grypus", "Phoque gris"),
    ("Phoca vitulina", "Phoque veau-marin"),
    ("Phoca groenlandica", "Phoque du Groënland"),
    ("Pusa hispida", "Phoque annelé (P. marbré)"),
    ("Cystophora cristata", "Phoque à crête (P. à capuchon)"),
    ("Erignathus barbatus", "Phoque barbu"),
    ("Mirounga leonina", "Eléphant de mer austral"),
    ("Hydrurga leptonyx", "Phoque léopard (Léopard de mer)"),
    ("Leptonychotes weddellii", "Phoque de Weddell"),
    ("Phocidae", "Phoque ind."),
    ("Arctocephalus forsteri", "Otarie à fourrure de Nouvelle-Zélande"),
    ("Arctocephalus gazella", "Otarie à fourrure des Kerguelen"),
    ("Arctocephalus tropicalis", "Otarie à fourrure subantartique"),
    ("Arctocephalus australis", "Otarie à fourrure australe"),
    ("Otariidae", "Otarie ind."),
    ("Odobenus rosmarus", "Morse"),
)

SCIENTIFIC_TO_COMMON: dict[str, str] = dict(SPECIES)
COMMON_TO_SCIENTIFIC: dict[str, str] = {common: sci for sci, common in SPECIES}

_SPACES = re.compile(r" +")


def normalize_common(label: str) -> str:
    return _SPACES.sub(" ", label.replace("\xa0", " ")).strip()


def from_common(label: str) -> tuple[str | None, str]:
    common = normalize_common(label)
    if "," in common:
        return None, common
    return COMMON_TO_SCIENTIFIC.get(common), common


def from_scientific(name: str | None) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    return name, SCIENTIFIC_TO_COMMON.get(name)
