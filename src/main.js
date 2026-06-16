import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

// =========================
// Scene
// =========================

const scene = new THREE.Scene()
scene.background = new THREE.Color(0x87ceeb)

// =========================
// Camera
// =========================

const camera = new THREE.PerspectiveCamera(
  75,
  window.innerWidth / window.innerHeight,
  0.1,
  2000
)

camera.position.set(100, 50, 100)

// =========================
// Renderer
// =========================

const renderer = new THREE.WebGLRenderer({
  antialias: true
})

renderer.setSize(
  window.innerWidth,
  window.innerHeight
)

const app = document.getElementById('app')
app.appendChild(renderer.domElement)

// =========================
// Controls
// =========================

const controls = new OrbitControls(
  camera,
  renderer.domElement
)

controls.enableDamping = true
controls.dampingFactor = 0.05

// =========================
// Lights
// =========================

const ambientLight =
  new THREE.AmbientLight(
    0xffffff,
    3
  )

scene.add(ambientLight)

const directionalLight =
  new THREE.DirectionalLight(
    0xffffff,
    3
  )

directionalLight.position.set(
  50,
  100,
  50
)

scene.add(directionalLight)

// =========================
// Variables
// =========================

let railwayModel = null

let trainObject = null

let northGate = null
let southGate = null

let trainRunning = false

// =========================
// Load GLB
// =========================

const loader = new GLTFLoader()

loader.load(

  '/3x.glb',

  (gltf) => {

    console.log('MODEL LOADED')

    railwayModel = gltf.scene

    scene.add(railwayModel)

    trainObject =
      railwayModel.getObjectByName(
        'TRAIN_ROOT_ANIMATED'
      )

    northGate =
      railwayModel.getObjectByName(
        'north_gate_arm_pivot'
      )

    southGate =
      railwayModel.getObjectByName(
        'south_gate_arm_pivot'
      )

    console.log('Train:', trainObject)
    console.log('North Gate:', northGate)
    console.log('South Gate:', southGate)

    railwayModel.traverse((obj) => {

      const n =
        obj.name.toLowerCase()

      if (
        n.includes('train') ||
        n.includes('gate') ||
        n.includes('signal')
      ) {

        console.log(obj.name)

      }

    })

  },

  undefined,

  (error) => {

    console.error(error)

  }

)

// =========================
// Start Train
// =========================

document
.getElementById('startTrain')
.addEventListener(
'click',
() => {

  trainRunning = true

  document
  .getElementById(
    'trainStatus'
  )
  .innerText = 'Running'

  document
  .getElementById(
    'gateStatus'
  )
  .innerText = 'Closed'

  if (northGate) {

    northGate.rotation.z =
      -1.2

  }

  if (southGate) {

    southGate.rotation.z =
      1.2

  }

}
)

// =========================
// Stop Train
// =========================

document
.getElementById('stopTrain')
.addEventListener(
'click',
() => {

  trainRunning = false

  document
  .getElementById(
    'trainStatus'
  )
  .innerText = 'Stopped'

  document
  .getElementById(
    'gateStatus'
  )
  .innerText = 'Open'

  if (northGate) {

    northGate.rotation.z = 0

  }

  if (southGate) {

    southGate.rotation.z = 0

  }

}
)

// =========================
// Close Gate
// =========================

document
.getElementById('closeGate')
.addEventListener(
'click',
() => {

  document
  .getElementById(
    'gateStatus'
  )
  .innerText = 'Closed'

  if (northGate) {

    northGate.rotation.z =
      -1.2

  }

  if (southGate) {

    southGate.rotation.z =
      1.2

  }

}
)

// =========================
// Open Gate
// =========================

document
.getElementById('openGate')
.addEventListener(
'click',
() => {

  document
  .getElementById(
    'gateStatus'
  )
  .innerText = 'Open'

  if (northGate) {

    northGate.rotation.z = 0

  }

  if (southGate) {

    southGate.rotation.z = 0

  }

}
)

// =========================
// Resize
// =========================

window.addEventListener(
'resize',
() => {

  camera.aspect =
    window.innerWidth /
    window.innerHeight

  camera.updateProjectionMatrix()

  renderer.setSize(
    window.innerWidth,
    window.innerHeight
  )

}
)

// =========================
// Animation Loop
// =========================

function animate() {

  requestAnimationFrame(
    animate
  )

  if (
    trainRunning &&
    trainObject
  ) {

    trainObject.position.y += 0.15

  }

  controls.update()

  renderer.render(
    scene,
    camera
  )

}

animate()