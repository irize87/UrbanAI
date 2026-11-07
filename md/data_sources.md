# 데이터 출처

| 항목 | 출처 |
|------|------|
| 건물·높이·용도·준공일 | 국토교통부 GIS건물통합정보 + 도로명주소 건물(VWorld) 보강 |
| 용도지역·공원녹지·지적 | VWorld 도시자연공원구역 + OSM 공원·숲·잔디·정원·하천 |
| 도로·가구·역·정류장 | OpenStreetMap 기여자 |
| 지형·등고선·경사도 | Copernicus DEM (CDSE Sentinel Hub Process API) |
| 항공사진·수목 | VWorld 항공영상 RGB jpeg HSV+ExG (수관·개체) |
| 가로수 | 서울열린데이터광장 streetTreeInfo (큰 도로 빈자리 보강, 월간 로컬 저장) |
| 일조·그림자 | NOAA 태양위치 + 높이 있는 건물 |

자세한 항목은 `json/data_sources.json`과 동일합니다.
