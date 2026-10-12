-- Consulta por publicação e limites geográficos, sem ler a carteira bruta.
CREATE INDEX fact_publication_position ON exposure.fact (calculation_id, lon, lat);
