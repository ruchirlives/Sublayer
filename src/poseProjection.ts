export function projectPosePoint(point:number[],matrix:number[][],width:number,height:number):[number,number]|null {
 const v=[...point,1];
 const clip=[0,1,2,3].map(c=>v.reduce((sum,value,r)=>sum+value*matrix[r][c],0));
 if(!clip.every(Number.isFinite)||clip[3]<=0||Math.abs(clip[2])>clip[3])return null;
 return [(clip[0]/clip[3]+1)*width/2,(1-clip[1]/clip[3])*height/2];
}
