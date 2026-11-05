"use client";
import { MapContainer, TileLayer, Circle, Marker, Popup, Polyline, useMap } from "react-leaflet";
import { useEffect } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Location, RequestRow, Shipment } from "@/lib/api";

const marker = (color: string) => L.divIcon({ className: "map-marker", html: `<span style="background:${color}"></span>`, iconSize: [24, 24] });
function MapMover({center}:{center:[number,number]}) { const map=useMap(); useEffect(()=>{map.setView(center);},[map,center]); return null; }
export default function GeoMap({ locations, requests, shipments, center, radiusKm }: {locations: Location[]; requests: RequestRow[]; shipments: Shipment[]; center: [number,number]; radiusKm: number}) {
  return <MapContainer center={center} zoom={11} scrollWheelZoom={false} style={{height: 440, width: "100%"}}>
    <MapMover center={center} />
    <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    <Circle center={center} radius={radiusKm*1000} pathOptions={{color: "#1a9389", weight: 1, fillOpacity: .04}} />
    {locations.map(l => <Marker key={l.id} position={[l.latitude,l.longitude]} icon={marker("#19766e")}><Popup><strong>{l.name}</strong><br />{l.kind.replaceAll("_"," ")}</Popup></Marker>)}
    {requests.map(r => <Marker key={r.id} position={[r.latitude,r.longitude]} icon={marker("#d66644")}><Popup><strong>{r.delivery_location}</strong><br />{r.priority} · {r.status}</Popup></Marker>)}
    {shipments.map(s => <Polyline key={s.id} positions={[[s.source_lat,s.source_lon],[s.target_lat,s.target_lon]]} color="#2c6598"><Popup>{s.source} → {s.destination} · {s.status}</Popup></Polyline>)}
  </MapContainer>;
}
