import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { Loader2 } from 'lucide-react';

interface Model3DViewerProps {
  url: string;       // Attachment file URL (with auth token query param)
  filename: string;  // Used to pick the loader by extension
}

/** Render a glb/obj/stl model with three.js + OrbitControls. */
export default function Model3DViewer({ url, filename }: Model3DViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loadingModel, setLoadingModel] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !url) return;

    setError('');
    setLoadingModel(true);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1d24);

    const camera = new THREE.PerspectiveCamera(
      50, container.clientWidth / container.clientHeight, 0.01, 1000
    );
    camera.position.set(0, 0, 5);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(5, 10, 7);
    scene.add(dirLight);
    scene.add(new THREE.GridHelper(10, 20, 0x444444, 0x2a2d35));

    // Center & fit camera to loaded object
    const fitToObject = (obj: THREE.Object3D) => {
      const box = new THREE.Box3().setFromObject(obj);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z) || 1;
      obj.position.sub(center); // center at origin
      camera.position.set(maxDim, maxDim * 0.8, maxDim * 1.6);
      camera.near = maxDim / 100;
      camera.far = maxDim * 100;
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.update();
    };

    const defaultMaterial = new THREE.MeshStandardMaterial({ color: 0x6d9eeb });
    const ensureMaterial = (obj: THREE.Object3D) => {
      obj.traverse((child) => {
        if (child instanceof THREE.Mesh && !child.material) {
          child.material = defaultMaterial;
        }
      });
    };

    const onLoaded = (obj: THREE.Object3D) => {
      ensureMaterial(obj);
      scene.add(obj);
      fitToObject(obj);
      setLoadingModel(false);
    };

    const onError = (e: unknown) => {
      console.error('Load 3D model failed:', e);
      setError('3D 模型加载失败');
      setLoadingModel(false);
    };

    const ext = filename.split('.').pop()?.toLowerCase() || '';
    if (ext === 'glb' || ext === 'gltf') {
      new GLTFLoader().load(url, (gltf) => onLoaded(gltf.scene), undefined, onError);
    } else if (ext === 'obj') {
      new OBJLoader().load(url, onLoaded, undefined, onError);
    } else if (ext === 'stl') {
      new STLLoader().load(
        url,
        (geometry) => {
          const mesh = new THREE.Mesh(geometry, defaultMaterial);
          onLoaded(mesh);
        },
        undefined,
        onError
      );
    } else {
      setError(`不支持的 3D 模型格式: .${ext}`);
      setLoadingModel(false);
    }

    let rafId = 0;
    const animate = () => {
      rafId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const handleResize = () => {
      if (!container.clientWidth || !container.clientHeight) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener('resize', handleResize);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener('resize', handleResize);
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [url, filename]);

  return (
    <div className="relative w-full h-[420px] rounded-md overflow-hidden">
      <div ref={containerRef} className="w-full h-full" />
      {loadingModel && !error && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40">
          <Loader2 className="h-6 w-6 animate-spin text-white" />
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-red-400">
          {error}
        </div>
      )}
      <div className="absolute bottom-2 right-2 text-[10px] text-white/50 select-none">
        拖拽旋转 · 滚轮缩放
      </div>
    </div>
  );
}
