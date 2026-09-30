-- 地图瓦片数据初始化
-- 添加常用地图瓦片服务到 UI 字模库

INSERT INTO adh_vis_components (code, name, category, style_config, source, is_builtin, is_active, sort_order, description) VALUES
('tile_osm_standard', 'OpenStreetMap 标准', 'map_tile', 
 '{"tileUrl": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "provider": "openstreetmap", "apiKey": ""}', 
 'system', 1, 1, 100, 'OpenStreetMap 标准瓦片，免费无需 API Key'),

('tile_osm_hot', 'OpenStreetMap Humanitarian', 'map_tile',
 '{"tileUrl": "https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", "provider": "openstreetmap", "apiKey": ""}',
 'system', 1, 1, 101, 'OpenStreetMap 人道主义风格瓦片'),

('tile_carto_light', 'CartoDB Positron (浅色)', 'map_tile',
 '{"tileUrl": "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", "provider": "carto", "apiKey": ""}',
 'system', 1, 1, 102, 'CartoDB 浅色底图，适合数据可视化'),

('tile_carto_dark', 'CartoDB Dark Matter (深色)', 'map_tile',
 '{"tileUrl": "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", "provider": "carto", "apiKey": ""}',
 'system', 1, 1, 103, 'CartoDB 深色底图，适合暗色主题'),

('tile_amap_standard', '高德地图标准', 'map_tile',
 '{"tileUrl": "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}", "provider": "amap", "apiKey": ""}',
 'system', 1, 1, 104, '高德地图标准瓦片（需配置 API Key 用于生产环境）'),

('tile_amap_satellite', '高德地图卫星', 'map_tile',
 '{"tileUrl": "https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}", "provider": "amap", "apiKey": ""}',
 'system', 1, 1, 105, '高德地图卫星影像瓦片'),

('tile_tianditu_vector', '天地图矢量', 'map_tile',
 '{"tileUrl": "http://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk={apiKey}", "provider": "tianditu", "apiKey": ""}',
 'system', 1, 1, 106, '天地图矢量瓦片（需要 API Key）'),

('tile_tianditu_satellite', '天地图卫星', 'map_tile',
 '{"tileUrl": "http://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk={apiKey}", "provider": "tianditu", "apiKey": ""}',
 'system', 1, 1, 107, '天地图卫星影像瓦片（需要 API Key）');
