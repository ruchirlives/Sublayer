import test from "node:test";
import assert from "node:assert/strict";
import {projectPosePoint} from "../src/poseProjection.ts";
test("bone overlay projects USD row-vector matrices with top-left screen coordinates",()=>{
 const matrix=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]];
 assert.deepEqual(projectPosePoint([0,0,0],matrix,800,600),[400,300]);
 assert.deepEqual(projectPosePoint([-1,1,0],matrix,800,600),[0,0]);
 assert.deepEqual(projectPosePoint([1,-1,0],matrix,800,600),[800,600]);
 assert.equal(projectPosePoint([0,0,2],matrix,800,600),null);
 const translated=structuredClone(matrix);translated[3][0]=0.5;
 assert.deepEqual(projectPosePoint([0,0,0],translated,800,600),[600,300]);
 const behind=structuredClone(matrix);behind[3][3]=-1;
 assert.equal(projectPosePoint([0,0,0],behind,800,600),null);
});
test("bone overlay projects orthographic viewProjection matrices without perspective divide distortion",()=>{
 // Orthographic frustum mapping x in [-2, 2], y in [-1, 1], z in [-10, 10] -> clip w = 1
 const ortho=[[0.5,0,0,0],[0,1,0,0],[0,0,-0.1,0],[0,0,0,1]];
 assert.deepEqual(projectPosePoint([1,0.5,-3],ortho,800,400),[600,100]);
 assert.deepEqual(projectPosePoint([1,0.5,3],ortho,800,400),[600,100]);
});
